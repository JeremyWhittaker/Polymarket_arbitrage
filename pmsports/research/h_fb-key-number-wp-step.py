"""Causal audit of the frozen football key-margin hypothesis; run `python ...py analyze`.

Original 3/6/7 direction and frozen DEV coefficients are preserved. Entry-price
proximity is replaced explicitly with a strictly prior raw reference. No-fills remain.
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
from pmsports.execution import TapeReplay
spec=importlib.util.spec_from_file_location('football_pairs',Path(__file__).with_name('h_fb-ladder-monotonicity-arb.py'))
H=importlib.util.module_from_spec(spec);spec.loader.exec_module(H)
SLUG='fb-key-number-wp-step';OUT=C.RESEARCH/('h_'+SLUG)
KEY=(3,6,7);CTRL=(2,5,8,9);OTHER=tuple(x for x in range(1,26) if x not in KEY+CTRL)


def features(d):
    reg=d.reg_s.fillna(0).clip(lower=0).to_numpy(float)
    return np.column_stack([d.margin,np.sqrt(reg),d.margin/np.sqrt(reg+1),d.up_off_home.fillna(.5),(d.lg=='cfb').astype(float)])


def causal_states(plays,games):
    """Prefix-only quality/state reconstruction; never inspect whole-game flags."""
    fields=['espn_id','m','condition_id','home_idx','lg','league','fee_rate','espn_date','closed_ts','home_abbr','away_abbr']
    meta=games.sort_values(['espn_id','condition_id'],kind='stable').drop_duplicates('espn_id')[fields]
    p=plays.merge(meta,on='espn_id',how='inner',validate='many_to_one').sort_values(['espn_id','play_idx'],kind='stable').reset_index(drop=True)
    plausible=np.isfinite(p.wallclock)&p.wallclock.ge(p.espn_date-3600)&p.wallclock.le(p.espn_date+8*3600)
    observed=p.wallclock.where(plausible).groupby(p.espn_id).cummax()
    previous=observed.groupby(p.espn_id).shift(1)
    p=p[plausible&(previous.isna()|p.wallclock.ge(previous))].copy()
    p['home_score_raw']=p.home_score;p['away_score_raw']=p.away_score
    g=p.groupby('espn_id',sort=False)
    p['prefix_score_back']=((g.home_score_raw.diff()<0)|(g.away_score_raw.diff()<0)).groupby(p.espn_id).cumsum()
    p['prefix_period_back']=(g.period.diff()<0).groupby(p.espn_id).cumsum()
    p['prefix_clock_fwd']=(g.period.diff().eq(0)&g.clock_s.diff().gt(0)).groupby(p.espn_id).cumsum()
    p['prefix_plays']=g.cumcount()+1
    p['prefix_ok']=p.prefix_score_back.le(2)&p.prefix_period_back.eq(0)&p.prefix_clock_fwd.le(4)&p.prefix_plays.ge(40)
    p['home_score']=g.home_score.cummax();p['away_score']=g.away_score.cummax()
    p['margin']=p.home_score-p.away_score
    p['raw_margin']=p.home_score_raw-p.away_score_raw
    p['current_score_agreement']=p.home_score_raw.eq(p.home_score)&p.away_score_raw.eq(p.away_score)
    g=p.groupby('espn_id',sort=False)
    p['stable']=p.margin.eq(g.margin.shift(1))&p.margin.eq(g.margin.shift(2))
    breaks=p['type'].isin(['End Period','End of Half','End of Game','Timeout','Official Timeout','Two-minute warning','End of Regulation'])
    p['up_off_home']=p.end_home.where(~breaks&p.end_home.ge(0)).groupby(p.espn_id).ffill()
    # Retrospective next-state censoring is confined to execution, never the signal filter.
    p['next_wc']=p.groupby('espn_id',sort=False).wallclock.shift(-1)
    return p


def state_eligible(p):
    """Current raw scores must agree with prefix repairs; never consult final game quality."""
    return (p.prefix_ok&p.stable&p.period.isin([3,4])&p.lg.isin(['nfl','cfb'])
            &p.margin.ne(0)&p.reg_s.gt(0)&p.current_score_agreement)


def prepare():
    raw=pd.read_parquet(C.DATA/'events/nfl/plays.parquet')
    games=pd.read_parquet(C.DATA/'events/nfl/games.parquet')
    p=causal_states(raw,games)
    mk=C.markets().set_index('condition_id')
    p['m_current']=p.condition_id.map(mk.m)
    if not p.m.eq(p.m_current).all():raise ValueError('NFL compact IDs changed; rebuild mapping')
    before_score_gate=p.prefix_ok&p.stable&p.period.isin([3,4])&p.lg.isin(['nfl','cfb'])&p.margin.ne(0)&p.reg_s.gt(0)
    b=p[state_eligible(p)].copy()
    cal=json.loads((OUT/'calib.json').read_text()) # refuse to silently retrain after explored holdout
    b['fit_home']=1/(1+np.exp(-(features(b)@np.asarray(cal['coef'])+cal['intercept'])))
    b['lead_home']=b.margin.gt(0);b['am']=b.margin.abs().astype(int)
    b['fit_lead']=np.where(b.lead_home,b.fit_home,1-b.fit_home)
    b['signal_ts']=b.wallclock.astype(float)
    b['expiry_ts']=np.minimum(b.signal_ts+120,b.next_wc.fillna(b.signal_ts+120))
    b['holdout']=b.espn_date.ge(H.HOLDOUT_TS);b['event_slug']=b.condition_id.map(mk.event_slug)
    b['event']=b.espn_id.astype(str);b['market']=b.condition_id.map(mk.market_slug)
    b['closed_ts']=b.condition_id.map(mk.closed_ts)
    f=C.fills(markets=b.m.unique(),columns=['m','s','ts','q','size','fee_rate'])
    f['print_id']='wp:'+f.index.astype(str)
    refs=pd.Series(np.nan,index=b.index);ages=refs.copy()
    for m,g in b.groupby('m',sort=False):
        tape=f[f.m==m].sort_values('ts',kind='stable')
        if tape.empty:continue
        ts=tape.ts.to_numpy(float);ix=np.searchsorted(ts,g.signal_ts.to_numpy(),'left')-1
        ok=ix>=0;chosen=ix.clip(0,len(tape)-1)
        home=np.where(tape.s.to_numpy()[chosen]==g.home_idx.to_numpy(),tape.q.to_numpy()[chosen],1-tape.q.to_numpy()[chosen])
        refs.loc[g.index]=np.where(ok,np.where(g.lead_home,home,1-home),np.nan)
        ages.loc[g.index]=np.where(ok,g.signal_ts.to_numpy()-ts[chosen],np.nan)
    b['ref_lead']=refs;b['ref_age']=ages
    legacy=pd.read_parquet(C.DATA/'events/nfl/panel.parquet',columns=['espn_id','play_idx','game_ok'])
    flagged=set(legacy.loc[~legacy.game_ok,'espn_id'])
    b['legacy_game_ok_audit']=~b.espn_id.isin(flagged)
    b.attrs['clock_audit']={'raw_play_rows':len(raw),'prefix_clock_rows':len(p),'raw_games':int(raw.espn_id.nunique()),
        'mapped_games':int(games.espn_id.nunique()),'legacy_panel_rows':len(legacy),'legacy_game_ok_excluded_games':len(flagged)}
    b.attrs['score_provenance']={'candidate_states_before_current_score_gate':int(before_score_gate.sum()),
        'rejected_current_score_disagreement':int((before_score_gate&~p.current_score_agreement).sum()),
        'rejected_repaired_key_margin_states':int((before_score_gate&~p.current_score_agreement&p.margin.abs().isin(KEY)).sum()),
        'eligible_states_after_current_score_gate':len(b),
        'gate':'Both current raw scores must equal their prefix-repaired values. No future rows or final game-quality flags select eligibility.'}
    return b,f,cal


def pick(rows,keys=KEY,band=.03,mirror=False):
    """Selection cannot depend on the availability/price of a future fill."""
    d=rows[rows.am.isin(keys)&rows.ref_age.le(300)&rows.ref_age.gt(0)&(rows.ref_lead-rows.fit_lead).abs().le(band)].copy()
    d=d.sort_values(['espn_id','play_idx'],kind='stable').drop_duplicates('espn_id')
    d['s']=np.where(d.lead_home,d.home_idx,1-d.home_idx).astype(int)
    if mirror:d['s']=1-d.s
    d['side']='BUY '+np.where(d.lead_home ^ mirror,d.home_abbr,d.away_abbr)
    d['budget_usd']=100.
    return d


def replay(rows,tape,slip=0.):
    mk=C.markets().set_index('m')
    orders=rows.copy()
    orders['y']=np.where(orders.s==0,orders.m.map(mk.y0),orders.m.map(mk.y1))
    if not orders.y.isin([0,.5,1]).all():raise ValueError('unknown payout orientation')
    out=TapeReplay(tape).replay(orders,delay_s=3,event_cap_usd=100,slip=slip)
    out['entry_ts']=out.fill_ts;out['exit_ts']=out.closed_ts;out['exit_price']=out.y
    out['exit_kind']=np.where(out.shares>0,'resolution','unfilled');out['execution_version']=H.VERSION
    return out


def bootstrap_tail_mass(d,n_boot=4000):
    """Ordinary bootstrap Pr(ROI<=0), centered on the sample; NOT a null-test p-value."""
    d=d[d.cost_usd>0]
    sums=d.groupby('event_slug')[['pnl_usd','cost_usd']].sum()
    if len(sums)<5:return None
    rng=np.random.default_rng(20260922);ix=rng.integers(0,len(sums),(n_boot,len(sums)))
    ratio=sums.pnl_usd.to_numpy()[ix].sum(axis=1)/sums.cost_usd.to_numpy()[ix].sum(axis=1)
    return float((ratio<=0).mean())


def control_criterion(control):
    """Apply the original dispositive control rule without claiming statistical certainty."""
    roi=control.get('roi')
    positive=None if roi is None or not np.isfinite(roi) else bool(roi>0)
    return {'criterion':'Original mandatory control: if the non-key 2/5/8/9 control is also profitable, the key-specific mechanism verdict is DEAD regardless of the main leg.',
        'status':'unavailable' if positive is None else 'failed' if positive else 'not_triggered',
        'mechanism_verdict':'DEAD' if positive else 'NOT ESTABLISHED',
        'control_positive_point_estimate':positive,'control_holdout':control,
        'uncertainty':'This prespecified point-estimate rule is not a significance test. Wide intervals can include loss; a positive control neither proves a reliable generic leading-team edge nor supports key-specific attribution. A nonpositive control alone cannot establish the primary mechanism.'}


def raw_margin_provenance(tr,coverage):
    matches=tr.current_score_agreement&tr.raw_margin.eq(tr.margin)
    return {**coverage,'selected_signals':len(tr),'selected_raw_score_and_margin_agree':int(matches.sum()),
        'selected_disagreement_rows':tr.loc[~matches,['espn_id','play_idx','home_score_raw','away_score_raw','home_score','away_score','raw_margin','margin']].to_dict('records'),
        'by_absolute_raw_margin':{str(int(m)):{tag:{'signals':int((g.holdout==(tag=='holdout')).sum()),
            'funded':int(((g.holdout==(tag=='holdout'))&g.cost_usd.gt(0)).sum())} for tag in ('dev','holdout')}
            for m,g in tr.groupby(tr.raw_margin.abs())}}


def write_report(result,description):
    pct=lambda x:'n/a' if x is None else f'{100*x:+.2f}%'
    criterion=result['mandatory_control_criterion'];provenance=result['raw_margin_provenance']
    lines=['# fb-key-number-wp-step','','**No executable edge established.**', '',
        f"**Original key-specific mechanism criterion: {criterion['mechanism_verdict']}** ({criterion['status']}).",'',description,'',
        'The original frozen smooth model, 3/6/7 margins and leading-team direction remain unchanged. Both current raw team scores must agree with their prefix-repaired scores before eligibility; this prevents a raw 27–20 state from becoming a manufactured 27–21 six-point signal. The gate uses only the current row and its prefix, never final game quality.', '',
        'The original 120-second bound limits entry expiry after the signal (also censored at the next historical state). The separate 300-second maximum age of the prior price reference is an added causal signal assumption, not a replacement of that entry bound. Entry must occur strictly after 3 seconds. Historical play/transaction clocks do not reveal actual public receipt latency, order-book depth or real cancelability.', '',
        'The historical holdout has already been explored. Intervals resample whole games and are nominal, without correction for the inspected variants.', '',
        '| Leg | Window | Signals | Funded | Capital incl. fees | Net P&L | ROI | 95% game CI |',
        '|---|---|---:|---:|---:|---:|---:|---|']
    for label,periods in [('Primary 3/6/7',result),('Mandatory control 2/5/8/9',result['variants']['control_2589'])]:
        for tag in ('dev','holdout'):
            s=periods[tag]
            lines.append(f"| {label} | {tag} | {s['signals']} | {s['bets']} | ${s['cost_usd']:.2f} | ${s['pnl_usd']:.2f} | {pct(s['roi'])} | {pct(s['ci_lo'])} to {pct(s['ci_hi'])} |")
    lines+=['','## Mandatory control and provenance','',criterion['criterion'],'',criterion['uncertainty'],'',
        f"{provenance['selected_raw_score_and_margin_agree']} of {provenance['selected_signals']} selected signals match both current raw scores and the raw margin. The score-agreement gate rejected {provenance['rejected_current_score_disagreement']} otherwise eligible state rows, including {provenance['rejected_repaired_key_margin_states']} repaired key-margin rows.",'',
        '| Absolute raw margin | DEV signals / funded | Holdout signals / funded |','|---|---:|---:|']
    for margin,counts in provenance['by_absolute_raw_margin'].items():
        lines.append(f"| {margin} | {counts['dev']['signals']} / {counts['dev']['funded']} | {counts['holdout']['signals']} / {counts['holdout']['funded']} |")
    lines+=['','## Limits','',result['direction_caveat'],'',
        'One signal per game, at most $100 fee-inclusive capital, one strictly later size-bounded print, settlement exit. Every no-fill remains in the full ledger. The collected/matched universe retains historical selection and missing-market biases. Agreement proves consistency with the cached raw feed, not that the feed itself was correct. The frozen model is preserved for an honest test of the original rule; no retraining or choice among margins follows the holdout.', '',
        '## Full audit results','','```json',json.dumps(H.clean(result),indent=2,allow_nan=False),'```','']
    (ROOT/'reports/research'/(SLUG+'.md')).write_text('\n'.join(lines))


def analyze():
    rows,tape,cal=prepare();selected=pick(rows);tr=replay(selected,tape)
    result={k:H.summary(tr[tr.holdout==(k=='holdout')]) for k in ('dev','holdout')}
    result['frozen_coefficients']=cal
    result['clock_audit']=rows.attrs.get('clock_audit',{})
    result['legacy_game_quality_descriptive']={str(ok):H.summary(g) for ok,g in tr.groupby('legacy_game_ok_audit')}
    result['legacy_before_repair']={'stable_rows':69664,'state_games':966,'holdout_signals':51,'holdout_funded':36,'holdout_roi':.13967725749985077,
        'note':'Rejected intermediate result still conditioned on complete-game quality; superseded by this raw-prefix run.'}
    result['bootstrap_tail_mass_below_zero']={k:bootstrap_tail_mass(tr[tr.holdout==(k=='holdout')]) for k in ('dev','holdout')}
    result['tail_mass_note']='Ordinary bootstrap tail mass is not a calibrated null-test p-value.'
    result['direction_caveat']='Buying leaders at margins 3 and 6 is not justified by the claimed plateau-step direction; 7 aligns but must be tested separately. Original directions remain visible, not selected away.'
    result['variants']={}
    for name,kw in [('control_2589',dict(keys=CTRL)),('neutral_other',dict(keys=OTHER)),('K37',dict(keys=(3,7))),('mirror',dict(mirror=True)),('band2c',dict(band=.02)),('band5c',dict(band=.05))]:
        r=replay(pick(rows,**kw),tape);result['variants'][name]={k:H.summary(r[r.holdout==(k=='holdout')]) for k in ('dev','holdout')}
    result['mandatory_control_criterion']=control_criterion(result['variants']['control_2589']['holdout'])
    result['raw_margin_provenance']=raw_margin_provenance(tr,rows.attrs.get('score_provenance',{}))
    stress=replay(selected,tape,slip=.01)
    result['stress_1c']={k:H.summary(stress[stress.holdout==(k=='holdout')]) for k in ('dev','holdout')}
    result['actual_margin_partition']={str(k):{tag:H.summary(g[g.holdout==(tag=='holdout')]) for tag in ('dev','holdout')} for k,g in tr.groupby('am')}
    result['coverage']={'state_rows':len(rows),'state_games':int(rows.espn_id.nunique()),'legacy_subset':'eventual-volume-selected compact moneyline collection; not a causal full universe',
        'expiry':'next-state retrospective censoring, not proof a pending sports order can be canceled',
        'raw_prefix':'Raw cached plays and matched-game mapping, not the future-filtered NFL panel. Prefix-only score/clock anomaly counts; plausible nondecreasing timestamp rule can reject later states after an outlier. No final quality flag or final game length controls signals. The legacy matched/collected universe still excludes uncollected and unmatched games.'}
    description='Reconstruct raw cached plays with prefix-only timestamps and score/clock quality; require both current raw scores to agree with repaired scores. Stable Q3/Q4 football leader at margin 3/6/7, positive regulation time, last prior raw price within 3c of the original frozen smooth DEV model and no older than 300s (an added causal reference-age assumption). First signal per game; no future has_price or entry-price filter. Buy strictly after 3s, before the next state or original 120s entry expiry, using one print and at most $100 actual capital. '
    description+=f"Original mandatory key-specific mechanism criterion: {result['mandatory_control_criterion']['mechanism_verdict']} ({result['mandatory_control_criterion']['status']}); the non-key control and its uncertainty are reported alongside the primary leg."
    H.save(SLUG,tr,result,description)
    write_report(result,description)
    print(json.dumps(H.clean(result),indent=2))


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1] not in ('analyze','run','prepare','extras'):raise SystemExit('Use analyze')
    analyze() # all periods rebuilt together; no legacy result-cache fallback
