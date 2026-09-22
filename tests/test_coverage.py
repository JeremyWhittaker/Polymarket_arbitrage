import json
import pandas as pd
from pmsports.research.common import RESEARCH
import pmsports.wallets.tapes as tapes

def test_cache_identity_preservation(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    # Create dummy universe
    u = pd.DataFrame({
        "condition_id": ["0xb", "0xb", "0xc", "0xc"],
        "token_id": ["t_b0", "t_b1", "t_c0", "t_c1"],
        "outcome_idx": [0, 1, 0, 1],
        "game_start_ts": [0, 0, 0, 0],
        "fee_rate": [0.0, 0.0, 0.0, 0.0],
        "payout": [1.0, 0.0, 1.0, 0.0],
        "family": ["soccer", "soccer", "nfl", "nfl"],
        "event_slug": ["e_b", "e_b", "e_c", "e_c"]
    })

    tapes_dir = tmp_path / "tapes"
    tapes_dir.mkdir()
    monkeypatch.setattr(tapes, "TAPES", tapes_dir)

    # Create dummy tapes
    schema = pa.schema([
        ("timestamp", pa.int64()), ("proxyWallet", pa.string()), ("side", pa.string()),
        ("outcomeIndex", pa.int64()), ("price", pa.float64()), ("size", pa.float64()), ("asset", pa.string())
    ])

    pq.write_table(pa.Table.from_arrays([
        [1], ["w_b"], ["BUY"], [0], [0.5], [10], ["t_b0"]
    ], schema=schema), tapes_dir / "0xb.parquet")

    pq.write_table(pa.Table.from_arrays([
        [2], ["w_c"], ["BUY"], [0], [0.5], [10], ["t_c0"]
    ], schema=schema), tapes_dir / "0xc.parquet")

    # First load
    t1 = tapes.load_trades(u)
    m1 = list(t1.condition_id.cat.categories)
    w1 = list(t1.proxyWallet.cat.categories)
    assert m1 == ["0xb", "0xc"]
    assert w1 == ["w_b", "w_c"]

    # Now add "0xa", which is lexically earlier
    u2 = pd.concat([u, pd.DataFrame({
        "condition_id": ["0xa", "0xa"], "token_id": ["t_a0", "t_a1"], "outcome_idx": [0, 1], "game_start_ts": [0, 0], "fee_rate": [0.0, 0.0],
        "payout": [1.0, 0.0], "family": ["soccer", "soccer"], "event_slug": ["e_a", "e_a"]
    })])
    pq.write_table(pa.Table.from_arrays([
        [3], ["w_a"], ["BUY"], [0], [0.5], [10], ["t_a0"]
    ], schema=schema), tapes_dir / "0xa.parquet")

    # Load with legacy_mcat
    t2 = tapes.load_trades(u2, legacy_mcat=m1, legacy_wcat=w1)
    m2 = list(t2.condition_id.cat.categories)
    w2 = list(t2.proxyWallet.cat.categories)

    assert m2[:2] == ["0xb", "0xc"]
    assert m2[2] == "0xa"
    assert w2[:2] == ["w_b", "w_c"]
    assert w2[2] == "w_a"

    # Make sure fill associations are unchanged
    # 0xb was m=0
    assert t2[t2.condition_id == "0xb"].condition_id.cat.codes.iloc[0] == 0
    # 0xa is m=2
    assert t2[t2.condition_id == "0xa"].condition_id.cat.codes.iloc[0] == 2

def test_removed_or_empty_legacy_market_does_not_shift_codes(tmp_path, monkeypatch):
    import numpy as np
    monkeypatch.setattr(tapes, 'TAPES', tmp_path)
    u = pd.DataFrame([dict(condition_id=c, outcome_idx=s, token_id=f'{c}{s}', payout=float(s == 0),
                          family='baseball', event_slug=c, fee_rate=.05, game_start_ts=10)
                      for c in ['a', 'b', 'c'] for s in [0, 1]])
    row = dict(timestamp=2, proxyWallet='new', side='BUY', outcomeIndex=0, price=.7, size=1, asset='c0')
    pd.DataFrame([row]).to_parquet(tmp_path/'c.parquet')
    pd.DataFrame([row]).iloc[:0].to_parquet(tmp_path/'b.parquet')
    result = tapes.load_trades(u, legacy_mcat=['a', 'b', 'c'], legacy_wcat=['old'])
    assert result.condition_id.cat.categories.tolist() == ['a', 'b', 'c']
    assert result.condition_id.cat.codes.tolist() == [2]
    assert result.proxyWallet.cat.codes.tolist() == [1]
    (tmp_path/'c.parquet').unlink()
    empty = tapes.load_trades(u, legacy_mcat=['a', 'b', 'c'], legacy_wcat=['old'])
    assert len(empty) == 0 and len(empty.condition_id.cat.categories) == 3


def test_windows_manifest_and_failed_fetch_preserve_previous(tmp_path, monkeypatch):
    import pytest
    from collections import namedtuple
    R = namedtuple('R', 'condition_id creation_ts game_start_ts closed_ts market_slug')
    start = pd.Timestamp('2026-01-01', tz='UTC').timestamp()
    r = R('c', start-8*86400, start, start+3*86400, 'm')
    assert tapes._window(r) == (int(start-8*86400), int(start+3*86400+1800))
    bounded = r._replace(creation_ts=float('nan'))
    assert tapes._window(bounded, '2025-12-01')[0] == pd.Timestamp('2025-12-01', tz='UTC').timestamp()
    monkeypatch.setattr(tapes, 'TAPES', tmp_path)
    monkeypatch.setattr(tapes.pm, 'trades', lambda *a: [])
    assert tapes._fetch(r) == 0
    assert tapes._fresh(r)
    assert not tapes._fresh(r._replace(closed_ts=r.closed_ts+86400))
    mp = tmp_path/'c.manifest.json'
    m = json.loads(mp.read_text()); m['request']['version'] = -1; mp.write_text(json.dumps(m))
    assert not tapes._fresh(r)
    before = (tmp_path/'c.parquet').read_bytes()
    def fail(*args): raise RuntimeError('network unavailable')
    monkeypatch.setattr(tapes.pm, 'trades', fail)
    with pytest.raises(RuntimeError): tapes._fetch(r)
    assert (tmp_path/'c.parquet').read_bytes() == before
    assert tapes.fetch_tapes(pd.DataFrame([r._asdict()]), max_markets=0)['scheduled'] == 0


def test_selection_default_keeps_low_and_unknown_future_volume():
    u = pd.DataFrame([dict(condition_id=str(i), market_type='moneyline', volume=v,
                          game_start_ts=1767225600, family='soccer') for i,v in enumerate([1,25000,50000,float('nan')])])
    assert len(tapes.select_markets(u)) == 4
    assert len(tapes.select_markets(u, min_volume=50000)) == 1


def test_favorite_decision_does_not_see_future_liquidity_and_caps_first_entry(tmp_path):
    from pmsports.analysis.favorites import _pregame_quote, favorite_bets
    start=10000
    path=tmp_path/'t.parquet'
    def row(ts, p, size=2): return dict(timestamp=ts, side='BUY', outcomeIndex=0, price=p, size=size, asset='a')
    early = [row(9000,.6),row(9200,.6),row(9300,.6)]
    pd.DataFrame(early+[row(9400,.99,1e6),row(9405,.8,1000),row(9406,.7,2),row(9500,.9,10000)]).to_parquet(path)
    q = _pregame_quote(path,start,('a','b'))
    assert q['pre_usd'] == 3.6 or abs(q['pre_usd']-3.6)<1e-10
    assert q['p0'] == .6 and q['n_fills'] == 3
    assert q['ask0'] == .7 and q['entry_size0'] == 2 and q['entry_ts0'] == 9406
    m = pd.DataFrame([{**q, 'o0':'A','o1':'B','y0':1.,'y1':0.,'fee_rate':.05,
                       'game_start_ts':start,'market_slug':'a','event_slug':'game'}])
    b=favorite_bets(m)
    assert abs(b.fav_stake.iloc[0]-2*(.7+.05*.7*.3)) < 1e-10
    pd.DataFrame(early+[row(9400,.01,1e8),row(9406,.7,2)]).to_parquet(path)
    q2=_pregame_quote(path,start,('a','b'))
    assert (q2['p0'], q2['pre_usd'],q2['ask0']) == (q['p0'],q['pre_usd'],q['ask0'])


def test_common_migrates_mapping_from_existing_tables(tmp_path, monkeypatch):
    import pmsports.research.common as c
    path=tmp_path/'markets.parquet'
    pd.DataFrame({'m':[1,0], 'condition_id':['a','z']}).to_parquet(path)
    monkeypatch.setattr(c,'MARKETS',path)
    assert c._market_codes() == ['z','a']


def test_terminal_prices_required_even_if_resolved():
    from pmsports.wallets.universe import _rows
    m=dict(outcomes='["A","B"]',clobTokenIds='["a","b"]',outcomePrices='["0.25","0.75"]',
           umaResolutionStatus='resolved',conditionId='c',createdAt='2025-01-01T00:00:00Z')
    assert _rows({'markets':[m]}) == []
    m['outcomePrices']='["0.5","0.5"]'
    assert len(_rows({'markets':[m]})) == 2


def test_cache_freshness_checks_source_outputs_and_build_marker(tmp_path, monkeypatch):
    import pytest
    import pmsports.research.common as c
    monkeypatch.setattr(c,'RESEARCH',tmp_path)
    paths=[tmp_path/n for n in ['fills.parquet','markets.parquet','wallets.parquet']]
    for key,path in zip(['FILLS','MARKETS','WALLETS'],paths):
        monkeypatch.setattr(c,key,path); path.write_bytes(b'good')
    current={'v':1}
    monkeypatch.setattr(c,'source_identity',lambda:current.copy())
    doc={'source':{'v':1},'outputs':{p.name:[p.stat().st_size,p.stat().st_mtime_ns] for p in paths}}
    (tmp_path/'manifest.json').write_text(json.dumps(doc))
    c.validate_cache(force=True)
    current['v']=2
    with pytest.raises(RuntimeError,match='stale'): c.validate_cache(force=True)
    current['v']=1; paths[0].write_bytes(b'corrupted')
    with pytest.raises(RuntimeError,match='mixed'): c.validate_cache(force=True)
    (tmp_path/'BUILDING').write_text('incomplete')
    with pytest.raises(RuntimeError,match='incomplete'): c.validate_cache(force=True)

def test_actual_common_build_preserves_legacy_ids_and_publishes_manifest(tmp_path, monkeypatch):
    import pmsports.research.common as c
    import pmsports.analysis.favorites as fav
    research=tmp_path/'research'; research.mkdir()
    wallets=tmp_path/'wallets'; wallets.mkdir()
    td=wallets/'tapes'; td.mkdir()
    monkeypatch.setattr(c,'DATA',tmp_path); monkeypatch.setattr(c,'RESEARCH',research)
    for attr,name in [('FILLS','fills'),('MARKETS','markets'),('WALLETS','wallets')]:
        monkeypatch.setattr(c,attr,research/f'{name}.parquet')
    monkeypatch.setattr(tapes,'TAPES',td); monkeypatch.setattr(fav,'TAPES',td); monkeypatch.setattr(fav,'OUT',wallets)
    u=pd.DataFrame([dict(condition_id=cid, outcome_idx=side, token_id=f'{cid}{side}',payout=float(side==0),
                        family='baseball',league='mlb',market_type='moneyline',event_slug=cid,market_slug=cid,
                        game_start_ts=10000,closed_ts=20000,fee_rate=.05,volume=100,neg_risk=False,
                        outcome=['A','B'][side]) for cid in ['a','z'] for side in [0,1]])
    u.to_parquet(wallets/'universe.parquet')
    # Earlier corpus assigned z=0; a did not exist. A rebuild must append a=1.
    pd.DataFrame({'m':[0], 'condition_id':['z']}).to_parquet(c.MARKETS)
    pd.DataFrame({'wallet':['old']}).to_parquet(c.WALLETS)
    for cid in ['a','z']:
        pd.DataFrame([dict(timestamp=9000+i*100,proxyWallet='new',side='BUY',outcomeIndex=0,
                           price=.6,size=2,asset=f'{cid}0') for i in range(3)]).to_parquet(td/f'{cid}.parquet')
    c.build()
    m=c.markets().set_index('condition_id')
    assert m.loc['z','m']==0 and m.loc['a','m']==1
    assert c.wallet_ids().tolist()==['old','new']
    fills=c.fills()
    assert fills.groupby('m').size().to_dict()=={0:3,1:3}
    assert c.validate_cache(force=True)['id_policy'].startswith('Append-only')
    assert not (research/'BUILDING').exists()
    # An empty former tape must preserve all old mapping slots on the next build.
    pd.read_parquet(td/'z.parquet').iloc[:0].to_parquet(td/'z.parquet')
    c.build()
    assert c.markets().set_index('condition_id').loc['z','m']==0
    assert set(c.fills().m)=={1}
