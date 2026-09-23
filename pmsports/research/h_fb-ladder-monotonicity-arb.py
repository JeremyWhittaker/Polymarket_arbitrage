"""Football ladder audit with causal, capacity-bounded, non-atomic transaction proxies.

Run `python pmsports/research/h_fb-ladder-monotonicity-arb.py analyze`.
No network calls. Existing capped tape coverage is reported, never treated as a universe.
The shared helpers here also serve the two companion football studies.
"""
from __future__ import annotations
import hashlib
import heapq
import json
import re
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pmsports.research import common as C
from pmsports.events.nfl import _norm, _score, ALIASES

SLUG = 'fb-ladder-monotonicity-arb'
OUT = C.RESEARCH / ('h_' + SLUG)
VERSION = 'football-causal-pairs-v2'
HOLDOUT_TS = pd.Timestamp('2026-07-01', tz='UTC').timestamp()
WINDOW = 120
FLOOR = .03
COLUMNS = ['id','period','date','sport','league','event','market','side','entry_ts',
           'entry_price','stake_usd','fee_usd','exit_kind','exit_ts','exit_price','payout','pnl_usd','roi','note','signal_ts']


def fee(p, rate):
    return rate * p * (1-p)


def orientation(o0, o1, home, away):
    """Contract-label proof; ambiguous names fail closed. Never infer from winners."""
    def keys(team):
        n = _norm(team)
        return {n, ALIASES.get(n,n)}
    scores = [(_score(o0,keys(home)), _score(o1,keys(away))),
              (_score(o1,keys(home)), _score(o0,keys(away)))]
    valid = [i for i, (h,a) in enumerate(scores) if h >= 2 and a >= 2]
    if len(valid) != 1:
        return None
    return valid[0]


def valid_payout(a,b):
    return (a,b) in ((0.,1.),(1.,0.),(.5,.5))


def moneylines():
    mk = C.markets()
    mk = mk[(mk.family == 'american_football') & (mk.market_type == 'moneyline')].copy()
    games = pd.read_parquet(C.DATA/'events/nfl/games.parquet')
    mapping = games.drop_duplicates('condition_id')[['condition_id','home_idx','espn_date','espn_id']]
    mk = mk.merge(mapping,on='condition_id',how='inner',validate='one_to_one')
    return mk[mk.home_idx.isin([0,1])].copy()


def discover():
    """All parseable matched rungs, no eventual-volume threshold or volume ranking."""
    u = C.universe(columns=['condition_id','family','market_type','market_slug','outcome_idx',
        'outcome','token_id','payout','closed_ts','fee_rate'])
    u = u[(u.family == 'american_football') & (u.market_type == 'spreads')]
    base = u.drop_duplicates('condition_id').set_index('condition_id')
    for field, prefix in [('outcome','o'),('token_id','token'),('payout','y')]:
        w = u.pivot(index='condition_id',columns='outcome_idx',values=field)
        for side in (0,1): base[prefix+str(side)] = w[side]
    ex = base.market_slug.str.extract(r'^(?P<event>.+)-spread-(?P<side>home|away)-(?P<whole>\d+)pt(?P<decimal>\d)$')
    base['side'] = ex.side
    base['line'] = pd.to_numeric(ex.whole) + pd.to_numeric(ex.decimal)/10
    base = base[base.side.notna()].reset_index()
    games = pd.read_parquet(C.DATA/'events/nfl/markets.parquet')
    fields = ['condition_id','event_slug','espn_id','espn_date','espn_home','espn_away',
              'final_home','final_away','lg','league']
    base = base.merge(games[fields].drop_duplicates('condition_id'),on='condition_id',how='left',validate='one_to_one')
    audit = {'parseable_rungs':len(base),'unknown_orientation':0,'invalid_settlement':0,'missing_game':0}
    rows = []
    for r in base.itertuples():
        if not np.isfinite(r.espn_date) or pd.isna(r.final_home) or pd.isna(r.final_away):
            audit['missing_game'] += 1; continue
        hi = orientation(r.o0,r.o1,r.espn_home,r.espn_away)
        if hi is None:
            audit['unknown_orientation'] += 1; continue
        yi = hi if r.side == 'home' else 1-hi
        y = r.y0 if yi == 0 else r.y1
        margin = r.final_home-r.final_away
        cover = (margin if r.side == 'home' else -margin) > r.line
        if not valid_payout(r.y0,r.y1) or (y != .5 and y != float(cover)):
            audit['invalid_settlement'] += 1; continue
        d = r._asdict(); d.update(yes_idx=yi,key=r.condition_id, is_ml=False,
            pay_yes=y,pay_no=1-y,holdout=r.espn_date >= HOLDOUT_TS)
        rows.append(d)
    out = pd.DataFrame(rows)
    audit['validated_rungs'] = len(out)
    return out,audit


def raw_tape(rungs):
    """Union both cached collections, deduplicated by original transaction identity."""
    folders = [C.RESEARCH/('h_'+s)/'trades' for s in [SLUG,'fb-ladder-key-number-gap']]
    frames=[]; available=set()
    for r in rungs.drop_duplicates('condition_id').itertuples():
        for folder in folders:
            path=folder/(r.condition_id+'.parquet')
            if not path.exists(): continue
            available.add(r.condition_id)
            d=pd.read_parquet(path)
            if d.empty: continue
            idx=np.where(d.asset.astype(str)==str(r.token0),0,np.where(d.asset.astype(str)==str(r.token1),1,-1))
            action=d.side.astype(str).str.upper()
            buy=action.eq('BUY')
            q=pd.to_numeric(d.price,errors='coerce')
            size=pd.to_numeric(d['size'],errors='coerce')
            ts=pd.to_numeric(d.timestamp,errors='coerce')
            good=(idx>=0)&action.isin(['BUY','SELL'])&q.between(0,1,inclusive='neither')&size.gt(0)&np.isfinite(size)&np.isfinite(ts)
            identity=[c for c in ['transactionHash','asset','side','price','size','proxyWallet','timestamp'] if c in d]
            pid=pd.util.hash_pandas_object(d[identity].astype(str),index=False).astype(str)
            frames.append(pd.DataFrame({'m':r.condition_id,'s':np.where(buy,idx,1-idx),
                'ts':ts,'q':np.where(buy,q,1-q),'size':size,'print_id':r.condition_id+':'+pid,
                'fee_rate':r.fee_rate})[good])
    cols=['m','s','ts','q','size','print_id','fee_rate']
    tape=pd.concat(frames,ignore_index=True).drop_duplicates('print_id').sort_values('ts',kind='stable') if frames else pd.DataFrame(columns=cols)
    return tape,available


def add_moneylines(rungs,tape):
    ml=moneylines(); ml=ml[ml.event_slug.isin(set(rungs.event_slug))]
    f=C.fills(markets=ml.m,columns=['m','s','ts','q','size','fee_rate'])
    codes=ml.set_index('m').condition_id
    f['print_id']='ml:'+f.index.astype(str) # one source row shared by both orientations
    f['m']=f.m.map(codes)
    additions=[]
    for r in ml.itertuples():
        if not valid_payout(r.y0,r.y1): continue
        for side in ('home','away'):
            yi=int(r.home_idx) if side=='home' else 1-int(r.home_idx)
            additions.append(dict(condition_id=r.condition_id,key=r.condition_id+'#'+side,
                event_slug=r.event_slug,espn_id=r.espn_id,espn_date=r.espn_date,side=side,line=.5,
                market_slug=r.market_slug,yes_idx=yi,y0=r.y0,y1=r.y1,pay_yes=r.y0 if yi==0 else r.y1,
                pay_no=r.y1 if yi==0 else r.y0,closed_ts=r.closed_ts,fee_rate=r.fee_rate,
                lg=str(r.league)[:3],league=r.league,is_ml=True,holdout=r.espn_date>=HOLDOUT_TS))
    return pd.concat([rungs,pd.DataFrame(additions)],ignore_index=True),pd.concat([tape,f],ignore_index=True)


def paths(tape):
    return {(m,int(s)):g.sort_values('ts',kind='stable') for (m,s),g in tape.groupby(['m','s'],sort=False)}


def screen(rungs,tape,window=WINDOW,floor=FLOOR):
    lookup=paths(tape); signals=[]; observed=0
    for (ev,side),g in rungs.groupby(['event_slug','side'],sort=False):
        rr=list(g.sort_values(['line','is_ml'],ascending=[True,False]).itertuples())
        for i,a in enumerate(rr[:-1]):
            for b in rr[i+1:]:
                if a.line >= b.line or a.condition_id==b.condition_id or str(a.espn_id)!=str(b.espn_id): continue
                x=lookup.get((a.condition_id,int(a.yes_idx))); y=lookup.get((b.condition_id,1-int(b.yes_idx)))
                if x is None or y is None: continue
                tx=x.ts.to_numpy(); ty=y.ts.to_numpy()
                candidate=np.union1d(tx,ty)
                ix=np.searchsorted(tx,candidate,'right')-1; iy=np.searchsorted(ty,candidate,'right')-1
                ok=(ix>=0)&(iy>=0)&(candidate<min(a.closed_ts,b.closed_ts))
                candidate,ix,iy=candidate[ok],ix[ok],iy[ok]
                if not len(candidate): continue
                px=x.q.to_numpy()[ix]; py=y.q.to_numpy()[iy]
                fresh=np.abs(tx[ix]-ty[iy])<=window
                observed+=int(fresh.any())
                edge=1-px-py-fee(px,a.fee_rate)-fee(py,b.fee_rate)
                hit=np.flatnonzero(fresh&(edge>=floor))
                if not len(hit): continue
                k=hit[0]; signal=float(candidate[k])
                later_fail=np.flatnonzero(~(fresh&(edge>=floor))[k+1:])
                stop=k+1+later_fail[0] if len(later_fail) else len(candidate)-1
                signals.append(dict(event=ev,event_slug=ev,side=side,lg=a.lg,holdout=a.holdout,
                    signal_ts=signal,expiry_ts=min(signal+3+window,a.closed_ts,b.closed_ts),
                    cid_a=a.condition_id,cid_b=b.condition_id,s_a=int(a.yes_idx),s_b=1-int(b.yes_idx),
                    ref_a=float(px[k]),ref_b=float(py[k]),rate_a=a.fee_rate,rate_b=b.fee_rate,
                    y_a=a.pay_yes,y_b=b.pay_no,closed_a=a.closed_ts,closed_b=b.closed_ts,
                    line_a=a.line,line_b=b.line,edge=float(edge[k]),market=a.market_slug+' | '+b.market_slug,
                    age_s=float(abs(tx[ix[k]]-ty[iy[k]])),persistence_s=float(candidate[stop]-signal),
                    persistence_censored=not len(later_fail),
                    event_a=ev,event_b=ev))
    return pd.DataFrame(signals),observed


def pair_replay(signals,tape,slip=0.,event_cap=100.):
    """Chronological independent legs, fixed quantity, one bounded sale proxy, residuals held.

    Each leg's dollar allocation is fixed from references before execution. A deadline
    triggers unwind of the actual imbalance; equal sizing is never inferred from future fills.
    Entry and exit proxies share one remaining-size pool, including moneyline orientations.
    """
    if slip<0 or event_cap<0: raise ValueError('negative slippage/cap')
    if signals.empty:
        return pd.DataFrame(columns=['signal_ts','holdout','event_slug','status','cost_usd','stake_usd','fee_usd','payout','pnl_usd','roi','entry_ts'])
    t=tape.drop_duplicates('print_id').sort_values(['m','s','ts'],kind='stable').reset_index(drop=True)
    groups={(m,int(s)):g.index.to_numpy() for (m,s),g in t.groupby(['m','s'],sort=False)}
    ts=t.ts.to_numpy(float); prices=t.q.to_numpy(float); sizes=t['size'].to_numpy(float)
    remaining=sizes.copy(); used={}; heap=[]; seq=0
    records=[]
    for i,r in enumerate(signals.to_dict('records')):
        rate=[r['rate_a'],r['rate_b']]; refs=[r['ref_a'],r['ref_b']]
        valid=(r['event_a']==r['event_b']==r['event'] and all(np.isfinite(rate)) and min(rate)>=0
            and all(np.isfinite(refs)) and min(refs)>0 and max(refs)<1
            and r['y_a'] in (0,.5,1) and r['y_b'] in (0,.5,1)
            and np.isfinite([r['signal_ts'],r['expiry_ts'],r['closed_a'],r['closed_b']]).all()
            and r['signal_ts']+3<r['expiry_ts'])
        unit=[p+fee(p,k) for p,k in zip(refs,rate)]
        target=100/sum(unit) if valid else 0
        r.update(target_shares=target,status='unfilled' if valid else 'ineligible',execution_version=VERSION,
            matched_shares=0.,matched_pnl=0.,unmatched_pnl=0.,stake_usd=0.,fee_usd=0.,payout=0.,pnl_usd=0.,
            cost_usd=0.,entry_ts=np.nan,exit_ts=max(r['closed_a'],r['closed_b']),entry_price=np.nan,exit_price=np.nan,exit_kind='unfilled',n_legs=0)
        for leg in ('a','b'):
            r.update({f'shares_{leg}':0.,f'entry_{leg}':np.nan,f'fill_ts_{leg}':np.nan,
                f'entry_fee_{leg}':0.,f'sold_{leg}':0.,f'sale_{leg}':0.,f'exit_fee_{leg}':0.,f'print_{leg}':None,
                f'exit_print_{leg}':None,f'sale_ts_{leg}':np.nan,f'residual_{leg}':0.})
        r['_budget_a']=target*unit[0];r['_budget_b']=target*unit[1]
        records.append(r)
        if valid:
            heapq.heappush(heap,(r['expiry_ts'],1,seq,'deadline',i,None,None));seq+=1
    def queue(kind,i,leg,after,expiry,start=0):
        nonlocal seq
        r=records[i]; side=int(r['s_'+leg]) if kind=='entry' else 1-int(r['s_'+leg])
        arr=groups.get((r['cid_'+leg],side),np.array([],int))
        pos=max(start,int(np.searchsorted(ts[arr],after,'right')))
        while pos<len(arr):
            j=arr[pos]
            if ts[j]>=expiry: return
            if remaining[j]>1e-12 and 0<prices[j]<1:
                heapq.heappush(heap,(ts[j],after,seq,kind,i,leg,(arr,pos,after,expiry)));seq+=1;return
            pos+=1
    for i,r in enumerate(records):
        if r['status']!='ineligible':
            for leg in ('a','b'): queue('entry',i,leg,r['signal_ts']+3,r['expiry_ts'])
    while heap:
        now,_,_,kind,i,leg,extra=heapq.heappop(heap);r=records[i]
        if kind=='deadline':
            matched=min(r['shares_a'],r['shares_b']);r['matched_shares']=matched
            for leg in ('a','b'):
                imbalance=r['shares_'+leg]-matched;r['residual_'+leg]=imbalance
                if imbalance>1e-12: queue('exit',i,leg,now+3,r['closed_'+leg])
            continue
        arr,pos,after,expiry=extra;j=arr[pos]
        if remaining[j]<=1e-12:
            queue(kind,i,leg,after,expiry,start=pos+1);continue
        rate=r['rate_'+leg]
        if kind=='entry':
            p=prices[j]+slip
            if not 0<p<1: continue
            dollars=min(r['_budget_'+leg],max(0,event_cap-used.get(r['event'],0)))
            qty=min(r['target_shares'],remaining[j],dollars/(p+fee(p,rate)))
            if qty<=1e-12: continue
            charge=qty*fee(p,rate);used[r['event']]=used.get(r['event'],0)+qty*p+charge
            r['shares_'+leg]=qty;r['entry_'+leg]=p;r['fill_ts_'+leg]=now;r['entry_fee_'+leg]=charge
            r['print_'+leg]=str(t.print_id.iloc[j])
        else:
            p=1-prices[j]-slip
            if not 0<p<1: continue
            qty=min(r['residual_'+leg],remaining[j])
            r['sold_'+leg]=qty;r['sale_'+leg]=qty*p;r['exit_fee_'+leg]=qty*fee(p,rate)
            r['sale_ts_'+leg]=now
            r['residual_'+leg]-=qty;r['exit_print_'+leg]=str(t.print_id.iloc[j])
        remaining[j]-=qty
    for r in records:
        if r['status']=='ineligible': continue
        matched=r['matched_shares']; nlegs=sum(r['shares_'+z]>0 for z in ('a','b'))
        r['status']='unfilled' if not nlegs else ('paired' if abs(r['shares_a']-r['shares_b'])<1e-9 else 'unmatched')
        r['n_legs']=nlegs
        for leg in ('a','b'):
            qty=r['shares_'+leg]
            if qty<=0: continue
            p=r['entry_'+leg]; entryfee=r['entry_fee_'+leg]
            r['stake_usd']+=qty*p;r['fee_usd']+=entryfee+r['exit_fee_'+leg]
            r['payout']+=r['sale_'+leg]+(qty-r['sold_'+leg])*r['y_'+leg]
            r['matched_pnl']+=matched*(r['y_'+leg]-p-entryfee/qty)
        r['cost_usd']=r['stake_usd']+r['fee_usd'];r['pnl_usd']=r['payout']-r['cost_usd']
        r['unmatched_pnl']=r['pnl_usd']-r['matched_pnl']
        r['roi']=r['pnl_usd']/r['cost_usd'] if r['cost_usd'] else np.nan
        times=[r['fill_ts_'+z] for z in ('a','b') if np.isfinite(r['fill_ts_'+z])]
        r['entry_ts']=min(times) if times else np.nan
        shares=r['shares_a']+r['shares_b'];r['entry_price']=r['stake_usd']/shares if shares else np.nan
        cash_times=[r['closed_'+z] if r['shares_'+z]-r['sold_'+z]>1e-10 else r['sale_ts_'+z] for z in ('a','b') if r['shares_'+z]>0]
        r['exit_ts']=max(cash_times) if cash_times else np.nan
        r['exit_price']=r['payout']/shares if shares else np.nan
        r['exit_kind']='exit_price' if nlegs else 'unfilled' # aggregate realized cash per acquired share; not a market quote
    return pd.DataFrame(records).drop(columns=['_budget_a','_budget_b'],errors='ignore')


def summary(d):
    funded=d[d.cost_usd>0] if len(d) else d
    res=dict(signals=len(d),bets=len(funded),no_fill=len(d)-len(funded),cost_usd=0.,pnl_usd=0.,roi=None,ci_lo=None,ci_hi=None)
    if not len(funded): return res
    roi,lo,hi=C.cluster_ci(funded.pnl_usd/funded.cost_usd,funded.event_slug,weights=funded.cost_usd,n_boot=4000,seed=20260922)
    res.update(cost_usd=float(funded.cost_usd.sum()),pnl_usd=float(funded.pnl_usd.sum()),roi=float(roi),
        ci_lo=float(lo) if np.isfinite(lo) else None,ci_hi=float(hi) if np.isfinite(hi) else None,
        events=int(funded.event_slug.nunique()),top3_pnl_usd=float(funded.nlargest(3,'pnl_usd').pnl_usd.sum()),
        drop_top3_pnl_usd=float(funded.nsmallest(max(0,len(funded)-3),'pnl_usd').pnl_usd.sum()))
    for c in ['matched_pnl','unmatched_pnl','matched_shares','sold_a','sold_b','residual_a','residual_b']:
        if c in funded:res[c]=float(funded[c].sum())
    return res


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    if isinstance(value,list):return [clean(v) for v in value]
    if isinstance(value,(float,np.floating)) and not np.isfinite(value):return None
    if isinstance(value,np.generic):return value.item()
    return value


def input_identity():
    files=[C.FILLS,C.MARKETS,C.DATA/'wallets/universe.parquet',C.DATA/'events/nfl/panel.parquet',
           C.DATA/'events/nfl/games.parquet',C.DATA/'events/nfl/markets.parquet',C.DATA/'events/nfl/plays.parquet',
           C.RESEARCH/'h_fb-key-number-wp-step/calib.json',C.RESEARCH/'h_fb-ladder-key-number-gap/scores.parquet']
    for slug in [SLUG,'fb-ladder-key-number-gap']:
        files.extend(sorted((C.RESEARCH/('h_'+slug)/'trades').glob('*.parquet')))
    return {str(p.relative_to(ROOT)):[p.stat().st_size,p.stat().st_mtime_ns] for p in files if p.exists()}


def save(slug,trades,result,description):
    out=C.RESEARCH/('h_'+slug);out.mkdir(parents=True,exist_ok=True)
    result.update(execution_version=VERSION,holdout_status='previously explored; not confirmatory',description=description)
    trades.to_parquet(out/'causal_trades.parquet',index=False)
    (out/'causal_results.json').write_text(json.dumps(clean(result),indent=2,allow_nan=False))
    manifest={'version':VERSION,'source_sha256':hashlib.sha256((ROOT/'pmsports/research'/('h_'+slug+'.py')).read_bytes()).hexdigest(),
        'shared_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'execution_sha256':hashlib.sha256((ROOT/'pmsports/execution.py').read_bytes()).hexdigest(),
        'inputs':input_identity()}
    (out/'causal_manifest.json').write_text(json.dumps(manifest,indent=2))
    lines=[f'# {slug}', '', '**No executable edge established.** These are corrected historical transaction proxies, not observed fills available to a new order.', '',description,'',
        'The July–September 2026 window and related variants were already inspected. Development is descriptive; the historical holdout is exploratory. Intervals are nominal game-cluster bootstrap intervals, without a multiple-testing correction.', '',
        '| Window | Signals | Funded | No fill | Capital incl. fees | Net P&L | ROI | 95% game CI |','|---|---:|---:|---:|---:|---:|---:|---|']
    for tag in ('dev','holdout'):
        s=result[tag];pct=lambda x:'n/a' if x is None else f'{100*x:+.2f}%'
        lines.append(f"| {tag} | {s['signals']} | {s['bets']} | {s['no_fill']} | ${s['cost_usd']:.2f} | ${s['pnl_usd']:.2f} | {pct(s['roi'])} | {pct(s['ci_lo'])} to {pct(s['ci_hi'])} |")
    lines+=['','## Execution and coverage','',
        'Signals use only prior/observed raw settlement timestamps, without subtracting an estimated chain lag. Entries occur strictly after the 3s eligibility delay and before expiry. An ESPN historical play clock is an optimistic observation assumption; public receipt time is unavailable here. Prints are neither asks/bids nor available depth. Each source transaction has one shared capacity allocation per strategy replay. Fees are charged from historical market metadata; no fee is silently invented.', '',
        'Pair quantities and per-leg dollar budgets are fixed from signal-time references. Each leg fills independently, using only the first relevant later print and its remaining size. A missing/partial hedge remains in total P&L. After the hedge window expires, one later opposite-side print can provide a bounded sale-price proxy; any unsold residual settles. Matched-share and unmatched P&L are reported separately. No pair is labeled executable arbitrage. Event capital is capped at $100 inclusive of entry fees and is not recycled after an unwind.', '',
        'The already collected moneyline and rung subsets have legacy eventual-volume and collection-budget biases. Removing volume eligibility does not backfill absent markets. No unseen market can be claimed tested. Metadata and historical tape timestamps do not prove when a market or signal first became publicly visible.', '',
        'Paired positions use one ledger row per signal. Displayed prices are aggregate realized cash per total acquired share, not quotes. `exit_kind=exit_price` records that cash accounting; residual settlement determines the final exit clock. Per-leg quantities, sales and residuals remain in the saved parquet audit. All signals, including no-fills, are in the complete desk ledger. Legacy `results*.json`/`bets*.parquet` files predate this repair and are not accepted inputs to the exporters.', '',
        '## Full audit results','', '```json',json.dumps(clean(result),indent=2,allow_nan=False),'```','']
    (ROOT/'reports/research'/(slug+'.md')).write_text('\n'.join(lines))
    export(slug)


def export(slug):
    out=C.RESEARCH/('h_'+slug)
    result=json.loads((out/'causal_results.json').read_text());manifest=json.loads((out/'causal_manifest.json').read_text())
    if result.get('execution_version')!=VERSION or manifest.get('version')!=VERSION:raise ValueError('legacy execution cache; rebuild study')
    for path,digest in [(ROOT/'pmsports/research'/('h_'+slug+'.py'),manifest['source_sha256']),(Path(__file__),manifest['shared_sha256'])]:
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('source changed; rebuild football study')
    if manifest.get('execution_sha256')!=hashlib.sha256((ROOT/'pmsports/execution.py').read_bytes()).hexdigest():raise ValueError('shared executor changed; rebuild football study')
    if manifest.get('inputs')!=input_identity():raise ValueError('football inputs changed; rebuild study')
    d=pd.read_parquet(out/'causal_trades.parquet').sort_values('signal_ts',kind='stable')
    rows=[]
    for i,r in enumerate(d.to_dict('records'),1):
        entry_ts=r.get('entry_ts')
        date_ts=entry_ts if pd.notna(entry_ts) else r['signal_ts']
        note=f"{r.get('status')}; decision {r['signal_ts']}; historical transaction proxy"
        if 'target_shares' in r:
            note+='; paired ledger prices are cash per total acquired share, not executable quotes; final exit clock includes residual settlement'
            note+=f"; prescribed {r['target_shares']:.5f} each; actual {r.get('shares_a',0):.5f}/{r.get('shares_b',0):.5f}; matched P&L {r.get('matched_pnl',0):.5f}; unmatched P&L {r.get('unmatched_pnl',0):.5f}; residual {r.get('residual_a',0):.5f}/{r.get('residual_b',0):.5f}"
        rows.append([i,'holdout' if r['holdout'] else 'dev',pd.to_datetime(date_ts,unit='s',utc=True).strftime('%Y-%m-%d'),
            'american_football',r.get('lg',''),r['event_slug'],r.get('market',''),(f"Yes({r.get('line_a')}) + No({r.get('line_b')}), {r.get('shares_a',0):.3f}/{r.get('shares_b',0):.3f} shares" if 'target_shares' in r else r.get('side','')),entry_ts,
            r.get('entry_price'),r['stake_usd'],r['fee_usd'],r.get('exit_kind','resolution'),r.get('exit_ts'),r.get('exit_price'),
            r['payout'],r['pnl_usd'],r.get('roi'),note,r['signal_ts']])
    doc=dict(slug=slug,title=slug.replace('fb-','Football ').replace('-',' '),group='Football causal repairs',sport='american_football',verdict='INCONCLUSIVE',
        hypothesis=result['description'],entry_rule='Prior observed signal; strict later, size-bounded transaction proxy. Every no-fill retained.',
        exit_rule='Settlement, including mismatched legs and bounded partial unwind where applicable.',
        cost_model='Historical fees and recorded capital allocations; no order-book execution claim.',
        caveats=['Historical holdout already explored. Legacy collection coverage is incomplete. Observed prints do not demonstrate available depth.'],
        headline={k:result[k] for k in ('dev','holdout')},truncated=False,n_total_trades=len(rows),columns=COLUMNS,rows=rows,
        report_path='reports/research/'+slug+'.md',code_path='pmsports/research/h_'+slug+'.py')
    dest=C.RESEARCH/'ledgers'/(slug+'.json');dest.parent.mkdir(exist_ok=True)
    temporary=dest.with_suffix('.tmp')
    temporary.write_text(json.dumps(clean(doc),allow_nan=False,separators=(',',':')))
    temporary.replace(dest)
    return doc


def secondary_payoff(margin):
    home_ml=1. if margin>0 else 0. if margin<0 else .5
    return 1-home_ml+float(margin>1.5)


def analyze():
    rungs,audit=discover();tape,available=raw_tape(rungs)
    audit.update(cached_rungs=len(available),missing_rungs=int((~rungs.condition_id.isin(available)).sum()))
    rungs=rungs[rungs.condition_id.isin(available)].copy()
    rungs,tape=add_moneylines(rungs,tape)
    signals,observed=screen(rungs,tape)
    tr=pair_replay(signals,tape); stressed=pair_replay(signals,tape,slip=.01)
    result={k:summary(tr[tr.holdout==(k=='holdout')]) for k in ('dev','holdout')}
    result.update(coverage=audit,observable_pairs=observed,signal_pairs=len(signals),
        void_leg_signals=int(((signals.y_a==.5)|(signals.y_b==.5)).sum()) if len(signals) else 0,
        median_observed_persistence_s=float(signals.persistence_s.median()) if len(signals) else None,
        stress_1c={k:summary(stressed[stressed.holdout==(k=='holdout')]) for k in ('dev','holdout')},
        secondary_payoff={str(m):secondary_payoff(m) for m in (-1,0,1,2)},
        secondary_verdict='NO(home ML)+YES(home by 1.5) pays zero at margin +1; not arbitrage. Tie adjustment cannot repair it.')
    decay=[]
    for window in (0,5,15,30,60,120,300,900,3600):
        sig,n=screen(rungs,tape,window=window)
        decay.append(dict(window_s=window,observable_pairs=n,signal_pairs=len(sig),rate=len(sig)/n if n else None))
    result['decay']=decay
    result['floor_variants']={}
    for floor in (.02,.05):
        sig,_=screen(rungs,tape,floor=floor);other=pair_replay(sig,tape)
        result['floor_variants'][str(floor)]={k:summary(other[other.holdout==(k=='holdout')]) for k in ('dev','holdout')}
    description='Scan same-side easier/harder football rungs for a 3c apparent cost gap using prints at most 120s apart; retain the first signal per rung pair. Equal-share payout is at least $1 only for consistent nonvoid contracts, but non-atomic partial execution is directional exposure.'
    save(SLUG,tr,result,description)
    print(json.dumps(clean(result),indent=2))


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1] not in ('analyze','run'):raise SystemExit('Use analyze; legacy cached/live/fetch modes were retired rather than reused as causal evidence.')
    analyze()
