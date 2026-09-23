import pytest
import numpy as np
import pandas as pd
import tempfile
from pathlib import Path
from pmsports.wallets import study, report

def _far_future_meta(t):
    """Explicit fixture cutoff, later than every intended execution window."""
    return pd.DataFrame({"closed_ts": 1e12}, index=t.condition_id.cat.categories)


def _mock_data():
    t_rows = [
        {"timestamp": 100, "condition_id": "c1", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 1.0, "fee_rate": 0.02, "in_play": False, "family": "soccer", "event_slug": "e1"},
        # This fill is before split (200), but closed_ts is 300 (after split). Should be excluded!
        {"timestamp": 150, "condition_id": "c2", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 0.0, "fee_rate": 0.02, "in_play": False, "family": "soccer", "event_slug": "e2"},
        # This fill is after split (200), but closed_ts is 300. In evaluation!
        {"timestamp": 250, "condition_id": "c3", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 1.0, "fee_rate": 0.02, "in_play": False, "family": "soccer", "event_slug": "e3"},
    ]
    t = pd.DataFrame(t_rows)
    for c in ["condition_id", "proxyWallet", "family", "event_slug"]:
        t[c] = t[c].astype("category")

    meta_rows = [
        {"condition_id": "c1", "closed_ts": 120.0},
        {"condition_id": "c2", "closed_ts": 300.0},
        {"condition_id": "c3", "closed_ts": 300.0},
    ]
    meta = pd.DataFrame(meta_rows).set_index("condition_id")
    return t, meta

def test_causality_boundary_study_run():
    t, meta = _mock_data()
    # Mock split to correspond to timestamp 200
    # pd.Timestamp(200, unit='s', tz="UTC")
    split_str = pd.Timestamp(200, unit='s', tz="UTC").strftime("%Y-%m-%d %H:%M:%S")

    # Run with meta
    res = study.run(t, split=split_str, label="all", meta=meta)

    # Should only include c1 in s1 (t1) since c2 closed after split
    s1 = res["s1"]
    # t1 should only have c1. So only 1 market.
    assert s1.loc["w1"].markets == 1
    assert s1.loc["w1"].staked == 5.0 # size 10 * q 0.5 = 5.0

def test_causality_boundary_walk_forward(monkeypatch):
    t, meta = _mock_data()
    # Walk forward from 1970-01-01 to 1970-01-02
    # m0 = 100, m1 = whatever
    # To test walk forward we need month boundaries. Let's adjust timestamps to months.
    ts1 = pd.Timestamp("2025-01-15", tz="UTC").timestamp()
    ts2 = pd.Timestamp("2025-01-20", tz="UTC").timestamp()
    ts3 = pd.Timestamp("2025-02-15", tz="UTC").timestamp()

    t_rows = [
        {"timestamp": ts1, "condition_id": "c1", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 1.0, "fee_rate": 0.0, "in_play": False, "family": "soccer", "event_slug": "e1"},
        {"timestamp": ts2, "condition_id": "c2", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 0.0, "fee_rate": 0.0, "in_play": False, "family": "soccer", "event_slug": "e2"},
        {"timestamp": ts3, "condition_id": "c3", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 1.0, "fee_rate": 0.0, "in_play": False, "family": "soccer", "event_slug": "e3"},
        {"timestamp": ts3 + 1, "condition_id": "c3", "proxyWallet": "w2", "size": 10.0, "side_idx": 0, "q": 0.55, "y": 1.0, "fee_rate": 0.0, "in_play": False, "family": "soccer", "event_slug": "e3"},
    ]
    t = pd.DataFrame(t_rows)
    for c in ["condition_id", "proxyWallet", "family", "event_slug"]:
        t[c] = t[c].astype("category")

    meta_rows = [
        {"condition_id": "c1", "closed_ts": ts1 + 10},
        {"condition_id": "c2", "closed_ts": pd.Timestamp("2025-02-05", tz="UTC").timestamp()},
        {"condition_id": "c3", "closed_ts": ts3 + 10},
    ]
    meta = pd.DataFrame(meta_rows).set_index("condition_id")

    # We set lookback to include Jan, evaluate on Feb.
    # For evaluate on Feb, split is 2025-02-01.
    # Lookback window will evaluate fills < 2025-02-01 AND closed_ts < 2025-02-01.
    # c2 closed_ts is 2025-02-05, so it should be EXCLUDED from ranking!

    # study.walk_forward needs a bit more rows, but we can lower TOP_K and MIN_MKTS by monkeypatching
    monkeypatch.setattr(study, "MIN_MKTS", 1)
    ranked = []
    original = study.skill.positions
    def capture_ranked(frame):
        ranked.append(frame.copy())
        return original(frame)
    monkeypatch.setattr(study.skill, "positions", capture_ranked)
    res = study.walk_forward(t, start="2025-01-01", end="2025-03-01", lookback_days=30, rules=("z", "whales"), delays=(0,), meta=meta)

    assert len(res) == 2
    assert len(ranked) == 1 and ranked[0].condition_id.astype(str).tolist() == ["c1"]
    # Equal independent policies get equal capacity; they do not compete with one another.
    assert res.staked.tolist() == [5.5, 5.5]
    assert res.trades.tolist() == [1, 1]
    assert res["pnl_per_$1"].tolist() == [4.5, 4.5]

def test_cache_invalidation(monkeypatch):
    import shutil
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        monkeypatch.setattr(report, "OUT", tdp)

        # Write dummy files
        (tdp / "universe.parquet").write_text("u")
        (tdp / "leaderboard.parquet").write_text("l")

        cache = tdp / "report_cache"
        cache.mkdir(exist_ok=True)

        # Test 1: Generate manifest
        man1 = report.make_manifest("2026", "2025", "2027")
        hash1 = report.get_code_hash()

        # Update input
        (tdp / "universe.parquet").write_text("uu")
        man2 = report.make_manifest("2026", "2025", "2027")

        u_key = str(tdp / "universe.parquet")
        assert man1["inputs"][u_key]["size"] != man2["inputs"][u_key]["size"]
        assert man1 != man2

def test_cache_hash_includes_shared_execution(monkeypatch, tmp_path):
    wallet_dir=tmp_path/'wallets'; wallet_dir.mkdir()
    monkeypatch.setattr(report,'__file__',str(wallet_dir/'report.py'))
    source=tmp_path/'execution.py'; source.write_text('version=1')
    first=report.get_code_hash()
    source.write_text('version=2')
    assert report.get_code_hash()!=first


def test_corrupt_quote_cannot_improve_ranking_or_trigger_copy():
    from pmsports.wallets import skill
    t,_=_mock_data()
    bad=t.iloc[[0]].copy();bad['q']=-10.;bad['size']=1e6;bad['timestamp']=90
    combined=pd.concat([t,bad],ignore_index=True)
    pd.testing.assert_frame_equal(skill.positions(combined),skill.positions(t))
    copied=skill.copy_prices(combined,bad,delays=(0,),meta=_far_future_meta(combined))
    assert len(copied)==1
    assert copied.cost_usd_d0.iloc[0]==0 and copied.prop_cost_usd_d0.iloc[0]==0
    assert pd.isna(copied.fill_ts_d0.iloc[0])


def test_wallet_comparison_does_not_sample_future_event_set(monkeypatch):
    t,_=_mock_data();rows=pd.concat([t.iloc[[2]]]*20,ignore_index=True)
    rows['event_slug']=pd.Categorical([f'e{i}' for i in range(20)])
    monkeypatch.setattr(study,'MAX_COPY_ROWS',1,raising=False)
    captured=[]
    monkeypatch.setattr(study.skill,'build_groups',lambda _,**kw: {'stub':True})
    def copy(t,selected,delay,**kw):
        captured.append(len(selected))
        return {'all':dict(roi=0.,ci_lo=0.,ci_hi=0.,trades=len(selected))}
    monkeypatch.setattr(study.skill,'copy_summary',copy)
    stats=pd.DataFrame(dict(markets=[20],staked=[100.],pnl=[1.],pnl_net=[.5]),index=['w1'])
    got=study.evaluate(rows,stats,['w1'],{},meta=_far_future_meta(rows))
    assert captured==[20]*(len(study.DELAYS)+1) and got['copy_d30_trades']==20


def test_monthly_default_keeps_more_than_old_40000_signal_limit(monkeypatch):
    t,_=_mock_data();history=t.iloc[[0]].copy();history['timestamp']=pd.Timestamp('2025-01-15',tz='UTC').timestamp()
    nxt=pd.concat([t.iloc[[2]]]*40001,ignore_index=True)
    nxt['timestamp']=pd.Timestamp('2025-02-15',tz='UTC').timestamp()+np.arange(len(nxt))
    both=pd.concat([history,nxt],ignore_index=True)
    meta=pd.DataFrame(dict(condition_id=['c1','c3'],closed_ts=[history.timestamp.iloc[0]+1,nxt.timestamp.max()+1])).set_index('condition_id')
    monkeypatch.setattr(study,'MIN_MKTS',1)
    monkeypatch.setattr(pd.DataFrame,'sample',lambda *a,**k: (_ for _ in ()).throw(AssertionError('future-event sampling')))
    monkeypatch.setattr(pd.Series,'sample',lambda *a,**k: (_ for _ in ()).throw(AssertionError('future-event sampling')))
    monkeypatch.setattr(study.skill,'build_groups',lambda _,**kw: {'stub':True})
    monkeypatch.setattr(study.skill,'copy_summary',lambda tape,selected,delay,**kw:
        {'all':dict(trades=len(selected),pnl=.1*len(selected),capital=float(len(selected)),in_play_share=0.)})
    got=study.walk_forward(both,'2025-02-01','2025-03-01',rules=('z',),delays=(0,),meta=meta)
    assert got.trades.tolist()==[40001] and got.staked.tolist()==[40001.]


def _copy_fixture():
    rows=[]
    for event, start, won in [('a',100.,1.),('b',1000.,0.)]:
        for delay, wallet, price in [(0.,'leader',.5),(6.,'other',.55),(31.,'other',.6),(61.,'other',.65)]:
            rows.append(dict(timestamp=start+delay,condition_id=event,proxyWallet=wallet,
                size=100.,side_idx=0,q=price,y=won,fee_rate=.05,in_play=False,
                family='soccer',event_slug=event))
    t=pd.DataFrame(rows)
    for col in ('condition_id','proxyWallet','family','event_slug'):
        t[col]=t[col].astype('category')
    return t


def test_evaluation_caches_small_summaries_and_matches_full_audit(monkeypatch):
    from pmsports.wallets import skill
    t=_copy_fixture();signals=t[t.proxyWallet=='leader']
    stats=skill.wallet_stats(skill.positions(t))
    full=skill.copy_prices(t,signals,delays=study.DELAYS,meta=_far_future_meta(t))
    expected={}
    for delay in study.DELAYS:
        summary=skill.summarize_copy(skill.copy_returns(full,delay))
        expected.update({f'copy_d{delay}_roi':summary['roi'],
            f'copy_d{delay}_ci':f"{summary['ci_lo']:+.3f}..{summary['ci_hi']:+.3f}",
            f'copy_d{delay}_trades':summary['trades']})
    summary=skill.summarize_copy(skill.copy_returns(full,30,stake='proportional'))
    expected.update(copy_d30_prop_roi=summary['roi'],
        copy_d30_prop_ci=f"{summary['ci_lo']:+.3f}..{summary['ci_hi']:+.3f}")
    calls=[];original=skill.copy_prices
    def capture(*args,**kwargs):
        calls.append((kwargs['delays'],kwargs['policies']))
        return original(*args,**kwargs)
    monkeypatch.setattr(skill,'copy_prices',capture)
    cache={}
    result=study.evaluate(t,stats,['leader'],cache,meta=_far_future_meta(t))
    assert calls==[((d,),(policy,)) for d in study.DELAYS
                  for policy in (('equal','proportional') if d==30 else ('equal',))]
    assert {key:result[key] for key in expected}==expected
    assert cache[('leader',)]==expected
    assert all(np.isscalar(value) for value in cache[('leader',)].values())
    def forbidden(*args,**kwargs):raise AssertionError('same selected wallets should reuse summary')
    monkeypatch.setattr(skill,'copy_prices',forbidden)
    monkeypatch.setattr(skill,'build_groups',forbidden)
    again=study.evaluate(t,stats,['leader','inactive'],cache)
    assert again['selected']==2 and again['active_p2']==1
    assert {key:again[key] for key in expected}==expected


def test_funded_move_is_strictly_between_five_and_ten_minutes(monkeypatch,tmp_path):
    from pmsports.wallets import skill
    t=_copy_fixture()
    # Signal A has a print at exactly five minutes, then at five minutes + one second.
    # Signal B only has prints on the excluded endpoints.
    t.loc[t.condition_id=='a','timestamp']=[100.,400.,401.,700.]
    t.loc[t.condition_id=='b','timestamp']=[1000.,1300.,1600.,1700.]
    t.loc[t.proxyWallet=='leader','size']=2000.
    signals=t[t.proxyWallet=='leader']
    audit=skill.copy_prices(t,signals,delays=(300,),meta=_far_future_meta(t))
    assert audit.eligible_ts_d300.tolist()==[400.,1300.]
    assert audit.expiry_ts_d300.tolist()==[700.,1600.]
    assert audit.fill_ts_d300.iloc[0]==401. and pd.isna(audit.fill_ts_d300.iloc[1])
    result=study.big_trade_signal(t,thresholds=(1000,),delays=(),meta=_far_future_meta(t))
    all_rows=result[result.phase=='all'].iloc[0]
    assert all_rows['funded_move_5_10min']==pytest.approx(.1)
    assert all_rows['move_funded_signals']==1 and all_rows['trades']==2
    assert 'price_move_5min' not in result
    monkeypatch.setattr(report,'OUT',tmp_path)
    rules=pd.DataFrame(columns=['family','rule','selected','active_p2','p1_roi','p1_median_z',
        'p2_roi','p2_roi_net_fee','copy_d0_roi','copy_d5_roi','copy_d30_roi','copy_d30_ci','copy_d60_roi','copy_d30_prop_roi'])
    text=report._render(t,pd.DataFrame(),rules,pd.DataFrame(),result,pd.DataFrame(),pd.DataFrame(),'2026-01-01')
    assert 'strictly after signal+300 seconds and before signal+600 seconds' in text
    assert 'only for actually funded equal-target orders' in text
    assert 'move_funded_signals' in text and '~0 means no information' not in text


# Frozen pre-batching oracle: retain the original stable accumulation exactly.
def _legacy_positions_reference(t: pd.DataFrame) -> pd.DataFrame:
    """Per (wallet, market) exposure, P&L and luck variance.

    numpy sort + reduceat on a combined integer key: memory stays ~a few arrays of len(t)
    (a pandas groupby over a wide temp frame OOMs at ~50M fills).
    """
    from pmsports.wallets import skill
    t = skill.valid_trades(t)
    if t.empty:
        out = pd.DataFrame({c: pd.Series(dtype=float) for c in ("cost", "pnl", "fee", "a", "b", "shares", "in_play", "n", "ts", "var", "pnl_net")})
        for target, source in (("proxyWallet", "proxyWallet"), ("condition_id", "condition_id"), ("family", "family"), ("event", "event_slug")):
            out[target] = t[source].iloc[:0].reset_index(drop=True)
        return out

    w = t.proxyWallet.cat.codes.to_numpy().astype(np.int64)
    m = t.condition_id.cat.codes.to_numpy().astype(np.int64)
    nm = int(m.max()) + 1 if len(m) else 1
    order = np.argsort(w * nm + m, kind="stable")
    key = (w * nm + m)[order]
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    del w, m

    def red(x):
        return np.add.reduceat(np.asarray(x)[order], starts) if len(starts) else np.array([])

    size = t["size"].to_numpy(np.float64)
    q = t.q.to_numpy(np.float64)
    side0 = t.side_idx.to_numpy() == 0
    out = {"cost": red(size * q), "pnl": red(size * (t.y.to_numpy(np.float64) - q)),
           "fee": red(skill.taker_fee(size, q, t.fee_rate.to_numpy(np.float64))),
           "a": red(np.where(side0, size, 0.0)), "b": red(np.where(side0, 0.0, size)),
           "p0w": red(size * np.where(side0, q, 1 - q)), "shares": red(size),
           "in_play": red(t.in_play.to_numpy(np.float64))}
    n = np.diff(np.r_[starts, len(key)])
    out["in_play"] = out["in_play"] / n
    out["n"] = n
    out["ts"] = np.minimum.reduceat(t.timestamp.to_numpy()[order], starts) if len(starts) else np.array([])
    del size, q, side0, order
    pos = pd.DataFrame(out)
    k0 = key[starts]
    wcode, mcode = (k0 // nm).astype(np.int32), (k0 % nm).astype(np.int32)
    p0 = (pos.p0w / pos.shares).clip(0.001, 0.999)
    pos["var"] = (pos.a - pos.b) ** 2 * p0 * (1 - p0)
    pos["pnl_net"] = pos.pnl - pos.fee
    pos["proxyWallet"] = pd.Categorical.from_codes(wcode, categories=t.proxyWallet.cat.categories)
    pos["condition_id"] = pd.Categorical.from_codes(mcode, categories=t.condition_id.cat.categories)
    # per-market attributes via first row of each market code
    mc = t.condition_id.cat.codes.to_numpy()
    first = np.full(len(t.condition_id.cat.categories), -1, dtype=np.int64)
    first[mc[::-1]] = np.arange(len(mc))[::-1]
    for c, src in (("family", "family"), ("event", "event_slug")):
        codes = t[src].cat.codes.to_numpy()[first[mcode]]
        pos[c] = pd.Categorical.from_codes(codes, categories=t[src].cat.categories)
    return pos.drop(columns=["p0w"])


def _position_fixture():
    rng=np.random.default_rng(917)
    rows=[]
    for market, count, payout in [('c3',17,1.),('c1',5,.5),('c2',23,0.),('c0',9,1.)]:
        for i in range(count):
            side=i%2
            rows.append(dict(condition_id=market,proxyWallet=['w3','w0','w2'][i%3],
                timestamp=1000+i,side_idx=side,q=float(rng.uniform(.01,.99)),
                size=float(rng.choice([1e-5,1.,1234.567,1e6])),y=payout if side==0 else 1-payout,
                fee_rate=float(rng.choice([0.,.03,.05])),in_play=bool(i%3),
                family='soccer' if i==0 else 'tennis',event_slug='event-'+market))
    t=pd.DataFrame(rows)
    for col,categories in [('condition_id',['c2','c0','c3','c1','unused-market']),
        ('proxyWallet',['w2','w3','w0','unused-wallet']),('family',['tennis','soccer','unused-sport']),
        ('event_slug',['event-c0','event-c3','event-c2','event-c1','unused-event'])]:
        t[col]=pd.Categorical(t[col],categories=categories,ordered=True)
    return t


@pytest.mark.parametrize('unsorted',[False,True])
def test_market_batches_preserve_exact_positions_stats_and_fdr(unsorted,monkeypatch):
    from pmsports.wallets import skill
    t=_position_fixture()
    t=t.sample(frac=1,random_state=71) if unsorted else t.sort_values('condition_id',kind='stable')
    # Deliberately retain duplicate/nonmonotone DataFrame indices.
    t.index=np.arange(len(t))%4
    expected=_legacy_positions_reference(t)
    seen=[];original=skill._positions_batch
    def record(batch):
        markets=set(batch.condition_id.astype(str))
        assert len(batch)<=20 or len(markets)==1
        assert not any(markets&previous for previous in seen)
        seen.append(markets)
        return original(batch)
    monkeypatch.setattr(skill,'_positions_batch',record)
    got=skill.positions(t,batch_rows=20)
    pd.testing.assert_frame_equal(got,expected,check_exact=True)
    assert len(seen)>=3 and got.n.sum()==len(t)
    old_stats=skill.wallet_stats(expected);new_stats=skill.wallet_stats(got)
    pd.testing.assert_frame_equal(new_stats,old_stats,check_exact=True)
    pd.testing.assert_series_equal(skill.fdr_survivors(new_stats.z),skill.fdr_survivors(old_stats.z))
    assert got.condition_id.cat.categories.tolist()==t.condition_id.cat.categories.tolist()
    assert got.proxyWallet.cat.categories.tolist()==t.proxyWallet.cat.categories.tolist()


def test_market_ordered_input_never_sorts_full_trade_count(monkeypatch):
    from pmsports.wallets import skill
    t=_position_fixture().sort_values('condition_id',kind='stable')
    expected=_legacy_positions_reference(t)
    sizes=[];original=skill.np.argsort
    def bounded(values,*args,**kwargs):
        sizes.append(len(values))
        assert len(values)<=23 # largest complete market; never all54 rows
        return original(values,*args,**kwargs)
    monkeypatch.setattr(skill.np,'argsort',bounded)
    pd.testing.assert_frame_equal(skill.positions(t,batch_rows=20),expected,check_exact=True)
    assert max(sizes)==23 and len(sizes)>=3


def test_batched_positions_preserve_empty_and_invalid_schema():
    from pmsports.wallets import skill
    t=_position_fixture()
    for rows in (t.iloc[:0],t.assign(q=-1)):
        got=skill.positions(rows,batch_rows=1)
        expected=_legacy_positions_reference(rows)
        pd.testing.assert_frame_equal(got,expected,check_exact=True)
        pd.testing.assert_frame_equal(skill.wallet_stats(got),skill.wallet_stats(expected),check_exact=True)
    with pytest.raises(ValueError,match='positive integer'):
        skill.positions(t,batch_rows=0)


def _monolithic_wallet_groups(t):
    """Original full-table constructor, retained as the exact replay oracle."""
    from pmsports.execution import TapeReplay
    tape = pd.DataFrame({'m': t.condition_id.cat.codes.to_numpy(), 's': t.side_idx.to_numpy(),
        'ts': t.timestamp.to_numpy(float), 'q': t.q.to_numpy(float),
        'size': t['size'].to_numpy(float), 'fee_rate': t.fee_rate.to_numpy(float),
        'w': t.proxyWallet.cat.codes.to_numpy()})
    return {'replay': TapeReplay(tape), 'wcat': t.proxyWallet.cat.categories,
            'mcat': t.condition_id.cat.categories,
            'closed_ts': _far_future_meta(t).closed_ts.to_numpy()}


def _connected_copy_fixture():
    rows = []
    for market, event in [('a','shared'), ('b','shared'), ('c','separate'), ('d','alone')]:
        for ts, wallet, size, q, side in [(100,'leader',1000,.5,0),
            (100,'leader',1000,.5,0), (101,'leader',1000,.55,0),
            (102,'other',2,.55,0), (102,'other',3,.6,0),
            (104,'other',1000,.6,0), (106,'other',1000,.7,0),
            (131,'other',1000,.8,0), (161,'other',1000,.9,0),
            (401,'other',1000,.9,0), (102,'other',1000,.1,1),
            (103,'other',-1,.5,0), (103,'other',1,-.5,0)]:
            rows.append(dict(timestamp=float(ts),condition_id=market,proxyWallet=wallet,
                size=float(size),side_idx=side,q=q,y=float(market in ['a','c']),fee_rate=.05,
                in_play=True,family='soccer',event_slug=event))
    t = pd.DataFrame(rows)
    for col in ('condition_id','proxyWallet','family','event_slug'):
        t[col] = t[col].astype('category')
    return t


@pytest.mark.parametrize('unsorted', [False, True])
def test_bounded_wallet_index_and_copy_match_monolithic(unsorted, monkeypatch):
    from pmsports.wallets import skill
    from pmsports.execution import TapeReplay
    t = _connected_copy_fixture()
    if unsorted:
        t = t.sample(frac=1, random_state=12)
    t.index = np.arange(len(t)) % 3
    reference = _monolithic_wallet_groups(t)
    built = []; original_init = TapeReplay.__init__
    def record_init(self, tape):
        built.append(len(tape))
        assert len(tape) <= 15
        original_init(self, tape)
    monkeypatch.setattr(TapeReplay, '__init__', record_init)
    groups = skill.build_groups(t, batch_rows=15, meta=_far_future_meta(t))
    assert len(built) == 4
    pd.testing.assert_frame_equal(groups['replay'].tape, reference['replay'].tape, check_exact=True)
    assert groups['replay'].groups == reference['replay'].groups
    signals = t[(t.proxyWallet == 'leader') & (t.timestamp == 100)].copy()
    # Retain a corrupt and a no-fill order in their original audit positions.
    signals = pd.concat([signals, signals.iloc[[0]].assign(q=-1),
                         signals.iloc[[0]].assign(timestamp=1000)])
    plan = skill._WalletTapeReplay.order_batches
    monkeypatch.setattr(skill._WalletTapeReplay, 'order_batches',
                        staticmethod(lambda orders: plan(orders, batch_orders=2)))
    expected = skill.copy_prices(t, signals, delays=(0,5,30,60,300), groups=reference)
    got = skill.copy_prices(t, signals, delays=(0,5,30,60,300), groups=groups)
    pd.testing.assert_frame_equal(got, expected, check_exact=True)
    statuses = set(got.status_d0) | set(got.status_d5)
    assert {'partial','event_cap','ineligible','unfilled'} <= statuses
    assert got.groupby('event_slug', observed=True).cost_usd_d0.sum().max() <= 100.00000001
    # All policies/delays reuse the constructed index; none rebuilds a market.
    assert len(built) == 4


def test_order_components_preserve_cross_event_liquidity_and_cap_priority():
    from pmsports.wallets import skill
    t = _connected_copy_fixture()
    reference = _monolithic_wallet_groups(t)['replay']
    replay = skill.build_groups(t, batch_rows=15, meta=_far_future_meta(t))['replay']
    # a links x/y and c links y/z: all three markets must stay together even
    # though event labels disagree with source metadata. Missing event falls back to m.
    orders = pd.DataFrame(dict(m=[0,0,1,2,2,3],s=[0]*6,
        event=['x','y','x','y','z',None],signal_ts=[100.]*6,
        leader_w=[t.proxyWallet.cat.categories.get_loc('leader')]*6,
        y=[1.]*6,budget_usd=[100.]*6),index=[4,4,2,2,1,1])
    batches = replay.order_batches(orders, batch_orders=2)
    assert len(batches) == 2 and batches[0].tolist() == [0,1,2,3,4]
    for delay in (0,5,30,300):
        expected = reference.replay(orders, delay_s=delay, horizon_s=300, event_cap_usd=100)
        got = replay.replay(orders, batches=batches, delay_s=delay, horizon_s=300, event_cap_usd=100)
        pd.testing.assert_frame_equal(got, expected, check_exact=True)
    # Explicit per-order expiry, eligibility and unknown-fee statuses are also unchanged.
    orders = orders.assign(receipt_ts=[99.,100.,100.,100.,100.,100.],
        expiry_ts=[102.,103.,102.,102.,102.,102.],budget_usd=[100.,0.,100.,100.,100.,100.])
    pd.testing.assert_frame_equal(replay.replay(orders,batches=batches),reference.replay(orders),check_exact=True)


def test_bounded_wallet_index_empty_and_invalid_prints():
    from pmsports.wallets import skill
    t = _connected_copy_fixture()
    for tape in (t.iloc[:0], t.assign(q=-1), t.assign(fee_rate=np.nan)):
        reference = _monolithic_wallet_groups(tape)
        groups = skill.build_groups(tape, batch_rows=15, meta=_far_future_meta(tape))
        pd.testing.assert_frame_equal(groups['replay'].tape,reference['replay'].tape,check_exact=True)
        for rows in (t.iloc[:0],t.iloc[[0,1]]):
            pd.testing.assert_frame_equal(skill.copy_prices(tape,rows,delays=(0,),groups=groups),
                skill.copy_prices(tape,rows,delays=(0,),groups=reference),check_exact=True)


@pytest.mark.parametrize('closure,expected',[(107.,True),(106.,False),(105.,False),(100.,False),(np.nan,False),(-1.,False),(np.inf,False)])
def test_wallet_closure_is_strict_preallocation_expiry(closure,expected):
    from pmsports.wallets import skill
    t=_copy_fixture();signals=t.iloc[[0]]
    meta=_far_future_meta(t);meta.loc['a','closed_ts']=closure
    before=t.copy(deep=True)
    groups=skill.build_groups(t,meta=meta)
    assert len(groups['closed_ts'])==len(t.condition_id.cat.categories)
    assert 'closed_ts' not in t and len(groups['replay'].tape)==len(t)
    got=skill.copy_prices(t,signals,delays=(0,5,30,300),groups=groups)
    assert len(got)==1
    for prefix in ('','prop_'):
        for delay in (0,5):
            assert bool(got[f'{prefix}cost_usd_d{delay}'].iloc[0]>0)==expected
        for delay in (30,300):
            assert got[f'{prefix}cost_usd_d{delay}'].iloc[0]==0
        for delay in (0,5,30,300):
            if got[f'{prefix}cost_usd_d{delay}'].iloc[0]>0:
                assert got[f'{prefix}fill_ts_d{delay}'].iloc[0]<closure
            else:
                assert pd.isna(got[f'{prefix}fill_ts_d{delay}'].iloc[0])
        if not np.isfinite(closure) or closure<=0:
            assert got[f'{prefix}status_d0'].iloc[0]=='ineligible'
            assert pd.isna(got[f'{prefix}expiry_ts_d0'].iloc[0])
    pd.testing.assert_frame_equal(t,before,check_exact=True)
    pd.testing.assert_frame_equal(skill.positions(t),skill.positions(before),check_exact=True)


def test_closed_order_does_not_consume_another_markets_event_cap():
    from pmsports.wallets import skill
    t=_copy_fixture();t['event_slug']=pd.Categorical(['shared']*len(t))
    t.loc[t.condition_id=='b','timestamp']=[100.,107.,132.,162.]
    t.loc[t.proxyWallet=='other','size']=1000.
    meta=_far_future_meta(t);meta.loc['a','closed_ts']=106.
    signals=t[t.proxyWallet=='leader']
    got=skill.copy_prices(t,signals,delays=(0,),meta=meta)
    assert got.cost_usd_d0.tolist()==[0.,100.]
    assert got.status_d0.tolist()==['unfilled','filled']
    assert got.fill_ts_d0.iloc[1]==107.
    assert got.prop_cost_usd_d0.tolist()==[0.,.5]
    # The raw later prints remain in the cached index and rankings.
    assert len(skill.build_groups(t,meta=meta)['replay'].tape)==len(t)


def test_wallet_copy_requires_explicit_closure_metadata_and_safe_market_lookup():
    from pmsports.wallets import skill
    t=_copy_fixture()
    with pytest.raises(ValueError,match='closure metadata'):
        skill.copy_prices(t,t.iloc[[0]],delays=(0,))
    with pytest.raises(ValueError,match='closure metadata'):
        skill.build_groups(t,meta=pd.DataFrame(index=t.condition_id.cat.categories))
    meta=_far_future_meta(t)
    with pytest.raises(ValueError,match='unique condition'):
        skill.build_groups(t,meta=pd.concat([meta,meta]))
    groups=skill.build_groups(t,meta=meta.drop(index='a'))
    got=skill.copy_prices(t,t.iloc[[0]],delays=(0,),groups=groups)
    assert got.status_d0.iloc[0]=='ineligible' and got.cost_usd_d0.iloc[0]==0
    unknown=t.iloc[[0]].copy();unknown['condition_id']=pd.Categorical(['not-on-tape'])
    got=skill.copy_prices(t,unknown,delays=(0,),groups=groups)
    assert got.status_d0.iloc[0]=='ineligible' and got.cost_usd_d0.iloc[0]==0
    with pytest.raises(ValueError,match='lack market closure'):
        skill.copy_prices(t,t.iloc[[0]],delays=(0,),groups={k:v for k,v in groups.items() if k!='closed_ts'})


@pytest.mark.parametrize('policy,prefix',[('equal',''),('proportional','prop_')])
def test_requested_sizing_policy_is_exact_and_skips_unused_replays(policy,prefix,monkeypatch):
    from pmsports.wallets import skill
    t=_connected_copy_fixture();signals=t[t.proxyWallet=='leader']
    groups=skill.build_groups(t,meta=_far_future_meta(t))
    both=skill.copy_prices(t,signals,delays=(0,5,30,300),groups=groups)
    calls=[];original=groups['replay'].replay
    def record(orders,**kw):
        calls.append(kw['delay_s']);return original(orders,**kw)
    monkeypatch.setattr(groups['replay'],'replay',record)
    chosen=skill.copy_prices(t,signals,delays=(0,5,30,300),groups=groups,policies=(policy,))
    assert calls==[0,5,30,300]
    pd.testing.assert_frame_equal(chosen,both[chosen.columns],check_exact=True)
    extras=[c for c in chosen if c not in signals]
    assert all(c.startswith('prop_') for c in extras) if prefix else not any(c.startswith('prop_') for c in extras)
    for bad in [(),('equal','equal'),('invalid',)]:
        with pytest.raises(ValueError,match='policies'):
            skill.copy_prices(t,signals,groups=groups,policies=bad)


def test_production_bigtrade_walk_and_decomposition_propagate_metadata_and_policies(monkeypatch):
    from pmsports.wallets import skill
    t=_copy_fixture();t.loc[t.proxyWallet=='leader','size']=2000.
    meta=_far_future_meta(t)
    seen=[];original=skill.copy_prices
    def capture(tape,rows,**kw):
        groups=kw.get('groups')
        assert groups is not None and 'closed_ts' in groups or kw.get('meta') is not None
        seen.append(kw.get('policies',('equal','proportional')))
        return original(tape,rows,**kw)
    monkeypatch.setattr(skill,'copy_prices',capture)
    study.big_trade_signal(t,thresholds=(1000,),delays=(0,),meta=meta)
    assert seen and set(seen)=={('equal',)}
    seen.clear()
    # One settled history market selects the same leader for a later month.
    history=t[t.condition_id=='a'].copy();history['timestamp']+=pd.Timestamp('2025-01-01',tz='UTC').timestamp()
    future=t[t.condition_id=='b'].copy();future['timestamp']+=pd.Timestamp('2025-02-01',tz='UTC').timestamp()
    full=pd.concat([history,future]);meta.loc['a','closed_ts']=history.timestamp.max()+1
    monkeypatch.setattr(study,'MIN_MKTS',1)
    study.walk_forward(full,'2025-02-01','2025-03-01',rules=('whales',),delays=(0,),meta=meta)
    assert seen and set(seen)=={('equal',)}
    seen.clear();monkeypatch.setattr(skill,'fdr_survivors',lambda _:pd.Series(['leader']))
    study.decompose_skilled(full,'2025-02-01',delays=(0,),meta=meta)
    assert seen==[('proportional',),('equal',)]


def _legacy_copy_summary(r, n_boot=1000, seed=11):
    if r.empty:
        return dict(trades=0,events=0,roi=np.nan,ci_lo=np.nan,ci_hi=np.nan)
    e=r.groupby('event_slug',observed=True).apply(lambda x:pd.Series(
        {'pnl':(x.copy_roi*x.w).sum(),'w':x.w.sum()}),include_groups=False)
    idx=np.random.default_rng(seed).integers(0,len(e),size=(n_boot,len(e)))
    boots=e.pnl.to_numpy()[idx].sum(1)/e.w.to_numpy()[idx].sum(1)
    return dict(trades=len(r),events=len(e),roi=float(e.pnl.sum()/e.w.sum()),
        ci_lo=float(np.percentile(boots,2.5)),ci_hi=float(np.percentile(boots,97.5)))


@pytest.mark.parametrize('stake',['equal','proportional'])
def test_streamed_summary_exact_with_connected_markets_phases_closure_and_duplicate_index(stake):
    from pmsports.wallets import skill
    t=_connected_copy_fixture().sample(frac=1,random_state=5)
    signals=t[(t.proxyWallet=='leader') & (t.timestamp==100)].copy()
    signals.index=np.arange(len(signals))%2
    signals['in_play']=np.arange(len(signals))%2==0
    # Inconsistent signal metadata links a market to two events; do not split it.
    signals['event_slug']=pd.Categorical(['x','y','x','z','x','y','q','q'],
                                        categories=['z','y','x','q'])
    signals=pd.concat([signals,signals.iloc[[0]].assign(q=-1),signals.iloc[[1]].assign(timestamp=1000)])
    groups=_monolithic_wallet_groups(t)
    groups['closed_ts']=groups['closed_ts'].copy()
    groups['closed_ts'][t.condition_id.cat.categories.get_loc('d')]=102.
    full=skill.copy_prices(t,signals,delays=(0,5,30,300),groups=groups,policies=(stake,))
    for delay in (0,5,30,300):
        result=skill.copy_summary(t,signals,delay,stake=stake,groups=groups,batch_orders=2,
            phases=('all','pregame','in_play'),cash_details=True,diagnostic_move=True)
        for phase in result:
            part=full if phase=='all' else full.loc[full.in_play.eq(phase=='in_play')]
            funded=skill.copy_returns(part,delay,stake=stake)
            expected=_legacy_copy_summary(funded)
            got={k:result[phase][k] for k in expected}
            pd.testing.assert_series_equal(pd.Series(got),pd.Series(expected),check_exact=True)
            assert result[phase]['pnl']==float((funded.copy_roi*funded.w).sum())
            assert result[phase]['capital']==float(funded.w.sum())
            assert result[phase]['in_play_share']==pytest.approx(float(funded.in_play.mean()),nan_ok=True)
            prefix='' if stake=='equal' else 'prop_'
            move=float((part[f'{prefix}q_d{delay}']-part.q).mean())
            assert result[phase]['funded_move_5_10min']==pytest.approx(move,nan_ok=True)


def test_bootstrap_chunking_and_narrow_reduction_are_exact_on_many_events():
    from pmsports.wallets import skill
    rng=np.random.default_rng(81)
    n=10_000
    # Several bootstrap chunks, with multiple rows/event and unequal tiny/large cash.
    r=pd.DataFrame({'event_slug':pd.Categorical(np.arange(n)%1201),
        'copy_roi':rng.normal(size=n),'w':10**rng.uniform(-9,4,n)})
    for i in range(30):r[f'unused_audit_{i}']=np.arange(n)
    assert skill.summarize_copy(r)==_legacy_copy_summary(r)


def test_tape_universe_projection_filters_all_cached_ids_and_preserves_duplicates(tmp_path,monkeypatch):
    from pmsports.wallets import tapes
    tape_dir=tmp_path/'tapes';tape_dir.mkdir()
    for cid in ('b','a','missing'):(tape_dir/f'{cid}.parquet').touch()
    rows=[]
    for cid,side in [('b',1),('unused',0),('a',0),('b',0),('a',1),('a',0)]:
        row={c:0. for c in report.TAPE_UNIVERSE_COLUMNS}
        row.update(condition_id=cid,outcome_idx=side,token_id=f'{cid}-{side}',family='soccer',
            event_slug=f'event-{cid}',market_slug=f'market-{cid}',outcome=str(side))
        rows.append(row)
    u=pd.DataFrame(rows);u['unrelated_metadata']='unused-large-description'*1000
    u.to_parquet(tmp_path/'universe.parquet',row_group_size=2,index=False)
    monkeypatch.setattr(report,'OUT',tmp_path);monkeypatch.setattr(tapes,'TAPES',tape_dir)
    expected=pd.read_parquet(tmp_path/'universe.parquet').loc[u.condition_id.isin(['a','b']),report.TAPE_UNIVERSE_COLUMNS].reset_index(drop=True)
    got=report.load_tape_universe()
    pd.testing.assert_frame_equal(got,expected,check_exact=True)
    for p in tape_dir.glob('*.parquet'):p.unlink()
    empty=report.load_tape_universe()
    pd.testing.assert_frame_equal(empty,expected.iloc[:0],check_exact=True)


def test_streamed_summary_retains_all_signals_with_bounded_audit_frames(monkeypatch):
    from pmsports.wallets import skill
    import weakref
    # More rows than one audit batch, with two markets sharing each event.
    n=60_000
    t=pd.DataFrame({'condition_id':pd.Categorical(np.arange(n)%200),
        'event_slug':pd.Categorical((np.arange(n)%200)//2),
        'proxyWallet':pd.Categorical(np.where(np.arange(n)<n//2,'leader','other')),
        'timestamp':np.arange(n,dtype=float),'size':np.full(n,10.),'q':np.full(n,.4),
        'side_idx':np.zeros(n,dtype=np.int8),'y':np.ones(n),'fee_rate':np.full(n,.05),
        'in_play':np.arange(n)%2==0,'family':pd.Categorical(['soccer']*n)})
    signals=t.iloc[:n//2]
    groups=skill.build_groups(t,meta=_far_future_meta(t))
    seen=[];previous=[];original=skill.copy_prices
    def capture(tape,rows,**kwargs):
        assert all(ref() is None for ref in previous)
        assert len(rows)<=1200
        assert kwargs['delays']==(0,) and kwargs['policies']==('equal',)
        seen.append(len(rows));out=original(tape,rows,**kwargs)
        previous.append(weakref.ref(out))
        return out
    monkeypatch.setattr(skill,'copy_prices',capture)
    summary=skill.copy_summary(t,signals,0,groups=groups,batch_orders=1200)
    assert sum(seen)==len(signals) and len(seen)>1
    assert set(summary)=={'all'} and all(np.isscalar(v) for v in summary['all'].values())


def test_completed_family_and_walk_scopes_resume_without_replaying(monkeypatch):
    base,meta=_mock_data();t=pd.concat([base]*20_000,ignore_index=True)
    monkeypatch.setattr(report,'FAMILIES',['soccer'])
    called=[];cache={}
    def cached(name,fn):
        if name not in cache:cache[name]=fn()
        return cache[name]
    def family(*args,**kw):
        called.append(args[3])
        return dict(n_p1_wallets=2,n_p2_wallets=3,n_both=1,n_tested=2,fdr_count=1,rho_z=.2,rho_roi=.3,
            placebo=pd.DataFrame({'p2_roi':[.1,.2]}),rules=pd.DataFrame({'rule':['z']}),
            deciles=pd.DataFrame(),s1=pd.DataFrame({'unneeded':[1]}),s2=pd.DataFrame({'unneeded':[2]}))
    monkeypatch.setattr(study,'run',family)
    split=pd.Timestamp(200,unit='s',tz='UTC').strftime('%Y-%m-%d %H:%M:%S')
    expected=report._families(t,None,split,meta,cached=cached)
    repeated=report._families(t,None,split,meta,cached=cached)
    assert called==['ALL','soccer']
    assert all('s1' not in value and 's2' not in value for value in cache.values())
    for a,b in zip(expected,repeated):pd.testing.assert_frame_equal(a,b,check_exact=True)
    called.clear()
    def walk(*args,**kw):
        called.append(len(args[0]));return pd.DataFrame({'month':['2025-01'],'trades':[1]})
    monkeypatch.setattr(study,'walk_forward',walk)
    expected=report._walk(base,'2025-01-01','2025-02-01',meta,cached=cached)
    repeated=report._walk(base,'2025-01-01','2025-02-01',meta,cached=cached)
    assert called==[len(base),len(base)]
    pd.testing.assert_frame_equal(expected,repeated,check_exact=True)


def test_streamed_components_use_tape_market_codes_for_missing_event_fallback():
    from pmsports.wallets import skill
    t=pd.DataFrame(dict(condition_id=pd.Categorical(['b','b','c','c'],categories=['unused','b','c']),
        proxyWallet=pd.Categorical(['leader','other','leader','other']),
        event_slug=pd.Categorical([None,None,1,1]),timestamp=[100.,101.,100.,101.],
        size=[1000.]*4,q=[.5]*4,side_idx=[0]*4,y=[1.]*4,fee_rate=[0.]*4,
        in_play=[False]*4,family=pd.Categorical(['soccer']*4)))
    rows=t[t.proxyWallet=='leader'].copy()
    rows['condition_id']=rows.condition_id.cat.remove_unused_categories()
    groups=skill.build_groups(t,meta=_far_future_meta(t))
    full=skill.copy_prices(t,rows,delays=(0,),policies=('equal',),groups=groups)
    assert full.cost_usd_d0.tolist()==[100.,0.]
    result=skill.copy_summary(t,rows,0,groups=groups,batch_orders=1,cash_details=True)['all']
    assert result['capital']==100. and result['trades']==1
