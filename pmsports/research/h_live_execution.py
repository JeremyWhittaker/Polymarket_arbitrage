"""Prespecified received-book checks of free-feed reaction and literal mean exits.

Run: python -m pmsports.research.h_live_execution --day 2026-09-19
No orders are sent. This replays observed depth, not exchange acceptance or queue.
"""
from __future__ import annotations
import argparse
import hashlib
import heapq
import json
import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd

from ..analysis.live import load_metadata
from ..book_replay import ReceivedBook
from ..collect import DATA_DIR, match_mlb
from ..http import get_json
from ..polymarket import GAMMA, taker_fee
from .common import cluster_ci

ROOT = Path(__file__).resolve().parents[2]
OUT = DATA_DIR / 'research' / 'live_execution'
LOG = logging.getLogger('pmsports')
STATE = ['inning', 'half', 'outs', 'bases', 'diff']


def terminal_payouts(g):
    if not g.get('closed') or g.get('umaResolutionStatus') != 'resolved':
        return {}
    tokens = json.loads(g['clobTokenIds']) if isinstance(g.get('clobTokenIds'), str) else g.get('clobTokenIds', [])
    pay = json.loads(g['outcomePrices']) if isinstance(g.get('outcomePrices'), str) else g.get('outcomePrices', [])
    return dict(zip(map(str, tokens), map(float, pay)))


def json_rows(path):
    if path.exists():
        with path.open() as f:
            for line in f:
                yield json.loads(line)


def prior_market_cells(panel, year):
    """One equal observation per historical game/cell, strictly prior seasons."""
    need = set(STATE + ['game_pk', 'event_date', 'mkt_p', 'mkt_staleness', 'exec_home_size'])
    if not need.issubset(panel):
        raise ValueError('rebuild the causal MLB panel before live execution analysis')
    x = panel[(panel.event_date.str[:4].astype(int) < year) & panel.mkt_staleness.le(120)].copy()
    one = x.groupby(STATE + ['game_pk'], observed=True).mkt_p.mean().reset_index()
    return one.groupby(STATE, observed=True).mkt_p.agg(mu='mean', sd='std', games='size').reset_index()


def metadata(day, out):
    m = load_metadata(day)
    schedule = pd.read_parquet(DATA_DIR / 'live' / day / 'schedule.parquet')
    m = match_mlb(m, schedule.assign(abstract_state='Final')).dropna(subset=['game_pk'])
    records = {}
    for row in m.to_dict('records'):
        cid = row['condition_id']
        file = out / f'{cid}.json'
        if not file.exists():
            response = get_json(f'{GAMMA}/markets', {'condition_ids': cid, 'closed': 'true'})
            file.write_text(json.dumps(response))
        response = json.loads(file.read_text())
        exact = [v for v in response if v.get('conditionId') == cid and v.get('closed')]
        row['y_home'] = row['y_away'] = np.nan
        row['resolution_status'] = 'unverified'
        if len(exact) == 1:
            g = exact[0]
            payouts = terminal_payouts(g)
            yh, ya = payouts.get(str(row['home_token'])), payouts.get(str(row['away_token']))
            if yh in (0., .5, 1.) and ya in (0., .5, 1.) and yh + ya == 1:
                row['y_home'], row['y_away'] = yh, ya
                row['resolution_status'] = 'actual token payout verified'
        records[int(row['game_pk'])] = row
    return records


def receipts(day, meta):
    days = [(pd.Timestamp(day) - pd.Timedelta(days=1)).strftime('%Y-%m-%d'), day]
    pm_to_pk = {str(int(g['pm_game_id'])): pk for pk, g in meta.items()}
    events, previous = [], {}
    boundary = pd.Timestamp(day, tz='UTC').timestamp() * 1000
    for d in days:
        for feed in ('mlb', 'sports'):
            for r in json_rows(DATA_DIR / 'live' / d / f'{feed}.jsonl'):
                if feed == 'mlb':
                    pk = int(r['game_pk'])
                    scores = (r['away'], r['home'])
                    state = dict(inning=r.get('inning'), half=str(r.get('half', '')).lower(), outs=r.get('outs'),
                                 bases=sum(v for k, v in [('first', 1), ('second', 2), ('third', 4)] if k in r.get('offense', [])),
                                 diff=r['home'] - r['away'])
                else:
                    msg = r['msg']
                    pk = pm_to_pk.get(str(msg.get('gameId')))
                    try:
                        scores = tuple(map(int, str(msg.get('score', '')).split('-')))
                        if len(scores) != 2:
                            continue
                    except ValueError:
                        continue
                    state = None
                if pk not in meta:
                    continue
                key = (feed, pk)
                prior = previous.get(key)
                previous[key] = scores
                if r['recv_ms'] < boundary:
                    continue
                if feed == 'mlb':
                    events.append(dict(kind='state', feed=feed, game_pk=pk, recv_ms=r['recv_ms'], state=state))
                if prior is None:
                    continue  # initial score is a baseline, never a surprise goal
                da, dh = scores[0] - prior[0], scores[1] - prior[1]
                if da > 0 or dh > 0:
                    side = 'home' if dh > 0 and da == 0 else ('away' if da > 0 and dh == 0 else None)
                    events.append(dict(kind='score', feed=feed, game_pk=pk, recv_ms=r['recv_ms'], side=side,
                                       score=f'{scores[0]}-{scores[1]}', reason='' if side else 'ambiguous_score_change'))
    return sorted(events, key=lambda e: (e['recv_ms'], e['kind']))


class Experiment:
    def __init__(self, meta, cells):
        self.meta = meta
        self.cells = {tuple(row[c] for c in STATE): row for row in cells.to_dict('records')}
        self.book = ReceivedBook()
        self.audit = []
        self.pending = []
        self.serial = 0
        self.attempted = set()
        self.positions = []

    def schedule(self, when, action, row):
        self.serial += 1
        heapq.heappush(self.pending, (when, self.serial, action, row))

    def row(self, event, policy, side=None):
        g = self.meta[event['game_pk']]
        r = dict(policy=policy, event=g['slug'], game_pk=event['game_pk'], side=side,
                 receipt_ts=event['recv_ms'] / 1000, signal_ts=event['recv_ms'] / 1000,
                 eligible_ts=np.nan, entry_ts=np.nan, entry_price=np.nan, shares=0., stake_usd=0., fee_usd=0.,
                 cost_usd=0., payout=0., exit_proceeds=0., exit_fee_usd=0., exit_shares=0.,
                 residual_shares=0., pnl_usd=0., roi=np.nan, status='unfilled', reason='',
                 resolution_status=g['resolution_status'], fee_rate=float(g['fee_rate']),
                 constraint_status='historical tick/minimum order constraints not recorded',
                 exit_ts=np.nan, target_attempted=False)
        self.audit.append(r)
        return r

    def receive(self, e):
        now = e['recv_ms']
        if e['kind'] == 'score':
            for delay in (1, 3, 5):
                policy = f'{e["feed"]}_score_{delay}s'
                r = self.row(e, policy, e['side'])
                key = (policy, e['game_pk'])
                if key in self.attempted:
                    r['reason'] = 'earlier_score_attempt_per_game'
                    continue
                self.attempted.add(key)
                if not e['side']:
                    r['reason'] = e['reason']
                    continue
                self.enter(r, now, delay)
        else:
            r = self.row(e, 'mean_reversion_3s')
            key = (r['policy'], e['game_pk'])
            if key in self.attempted:
                r['reason'] = 'earlier_mean_signal_per_game'
                return
            cell = self.cells.get(tuple(e['state'][c] for c in STATE))
            if not cell or cell['games'] < 30 or not math.isfinite(cell['sd']):
                r['reason'] = 'insufficient_prior_cell'
                return
            r.update(prior_games=cell['games'], mu_home=cell['mu'], sd=cell['sd'])
            options = []
            for side, mu in (('home', cell['mu']), ('away', 1 - cell['mu'])):
                token = str(self.meta[e['game_pk']][side + '_token'])
                ask = self.book.quote(token, policy=r['policy'])
                if math.isfinite(ask) and ask < mu - cell['sd']:
                    options.append((mu - ask, side, mu))
            if not options:
                r['reason'] = 'no_one_sd_discount_or_no_ask'
                return
            _, side, mu = max(options)
            self.attempted.add(key)
            r.update(side=side, target=mu)
            self.enter(r, now, 3, ceiling=mu)

    def enter(self, r, now, delay, ceiling=.999):
        g = self.meta[r['game_pk']]
        token = str(g[r['side'] + '_token'])
        ask = self.book.quote(token, policy=r['policy'])
        if token not in self.book.initialized or not math.isfinite(ask):
            r['reason'] = 'no_received_ask_at_signal'
            return
        if now - self.book.last_ms[token] > 5000:
            r['reason'] = 'stale_ask_at_signal'
            return
        limit = min(.999, ceiling, ask + .01)
        r.update(token=token, limit_price=limit, signal_ask=ask, eligible_ts=now / 1000 + delay,
                 target_shares=100 / (limit + float(taker_fee(1., limit, r['fee_rate']))))
        self.schedule(now + delay * 1000, 'entry', r)

    def execute(self, now, action, r):
        if action == 'entry':
            result = self.book.cross(r['token'], now, policy=r['policy'], limit=r['limit_price'],
                                     shares=r['target_shares'], fee_rate=r['fee_rate'])
            r.update(status=result['status'], reason=result['reason'], entry_book_ts=(result['book_ms'] or 0) / 1000)
            if result['shares']:
                r.update(shares=result['shares'], residual_shares=result['shares'], stake_usd=result['gross_usd'],
                         fee_usd=result['fee_usd'], cost_usd=result['cash_usd'], entry_price=result['price'], entry_ts=now / 1000)
                if r['policy'] == 'mean_reversion_3s':
                    self.positions.append(r)
                    self.schedule(now + 60000, 'timeout_signal', r)
        elif action == 'timeout_signal':
            if r['residual_shares'] > 1e-10:
                # Fixed aggressive limit gives an honest crossing sensitivity; no invented midpoint exit.
                self.schedule(now + 3000, 'timeout_exit', r)
        elif r['residual_shares'] > 1e-10:
            result = self.book.cross(r['token'], now, policy=r['policy'], buy=False,
                shares=r['residual_shares'], limit=r['target'] if action == 'target_exit' else .001,
                fee_rate=r['fee_rate'])
            r['exit_proceeds'] += result['cash_usd']
            r['exit_fee_usd'] += result['fee_usd']
            r['exit_shares'] += result['shares']
            r['residual_shares'] -= result['shares']
            r['last_exit_reason'] = action + ':' + (result['reason'] or result['status'])
            if result['shares']:
                r['exit_ts'] = now / 1000

    def on_book(self, now, changed):
        for r in self.positions:
            if r['residual_shares'] <= 1e-10 or r['target_attempted'] or r['token'] not in changed:
                continue
            # Timeout has its own predetermined order; no target attempt after it.
            if now >= (r['entry_ts'] + 60) * 1000:
                continue
            bid = self.book.quote(r['token'], False, r['policy'])
            if math.isfinite(bid) and bid >= r['target']:
                r['target_attempted'] = True
                self.schedule(now + 3000, 'target_exit', r)

    def finish(self):
        for _, _, action, r in self.pending:
            if action == 'entry':
                r['reason'] = 'capture_ended_before_eligibility'
            elif r['residual_shares'] > 1e-10:
                r['exit_censor_reason'] = 'capture_ended_before_' + action
        for r in self.audit:
            y = self.meta[r['game_pk']].get('y_' + str(r['side']), np.nan)
            r['settlement_price'] = y
            if r['shares'] > 0:
                r['payout'] = r['residual_shares'] * y if r['residual_shares'] > 1e-10 else 0.
                r['pnl_usd'] = r['exit_proceeds'] + r['payout'] - r['cost_usd']
                r['deployed_usd'] = r['cost_usd'] + r['exit_fee_usd']
                r['roi'] = r['pnl_usd'] / r['deployed_usd']
            else:
                r['deployed_usd'] = 0.
        if not self.audit:
            return pd.DataFrame(columns=['policy','cost_usd','deployed_usd','roi','game_pk','pnl_usd','eligible_ts','status','exit_shares','residual_shares','reason'])
        return pd.DataFrame(self.audit)


def run(day):
    out = OUT / day
    out.mkdir(parents=True, exist_ok=True)
    meta = metadata(day, out)
    panel = pd.read_parquet(DATA_DIR / 'mlb' / 'panel.parquet')
    cells = prior_market_cells(panel, int(day[:4]))
    cells.to_parquet(out / 'prior_cells.parquet', index=False)
    events = receipts(day, meta)
    sim = Experiment(meta, cells)
    i, n = 0, 0
    end = 0
    previous_day = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    sources = []
    for d in (previous_day, day):
        path = DATA_DIR / 'live' / d / 'clob.jsonl'
        sources.append(path)
        for raw in json_rows(path):
            now = raw['recv_ms']
            while True:
                receipt_ms = events[i]['recv_ms'] if i < len(events) else math.inf
                timer_ms = sim.pending[0][0] if sim.pending else math.inf
                if min(receipt_ms, timer_ms) >= now:
                    break
                if timer_ms <= receipt_ms:
                    when, _, action, row = heapq.heappop(sim.pending)
                    sim.execute(when, action, row)
                else:
                    sim.receive(events[i]); i += 1
            changed = sim.book.apply(now, raw['msg'])
            sim.on_book(now, changed)
            end = now; n += 1
            if n % 250000 == 0:
                LOG.info('processed %s received book messages; %s audit rows', n, len(sim.audit))
    # Process only events inside observed capture. Later receipts cannot borrow old depth.
    while i < len(events) and events[i]['recv_ms'] <= end:
        while sim.pending and sim.pending[0][0] <= events[i]['recv_ms']:
            when, _, action, row = heapq.heappop(sim.pending); sim.execute(when, action, row)
        sim.receive(events[i]); i += 1
    while sim.pending and sim.pending[0][0] <= end:
        when, _, action, row = heapq.heappop(sim.pending); sim.execute(when, action, row)
    audit = sim.finish()
    audit.to_parquet(out / 'signals.parquet', index=False)
    rows = []
    for policy, g in audit.groupby('policy'):
        f = g[g.cost_usd > 0]
        resolved = f[np.isfinite(f.roi)]
        avg, lo, hi = cluster_ci(resolved.roi, resolved.game_pk.to_numpy(), weights=resolved.deployed_usd.to_numpy()) if len(resolved) else (np.nan,) * 3
        game = resolved.groupby('game_pk').pnl_usd.sum()
        rows.append(dict(policy=policy, observations=len(g), eligible_orders=int(g.eligible_ts.notna().sum()),
                         fills=len(f), games=f.game_pk.nunique(), partial=int(f.status.eq('partial').sum()),
                         capital_usd=f.deployed_usd.sum(), resolved_capital_usd=resolved.deployed_usd.sum(),
                         pnl_usd=resolved.pnl_usd.sum(), roi=avg, ci_lo=lo, ci_hi=hi,
                         unresolved=int(f.roi.isna().sum()), top_game_pnl=game.max(), median_capital=f.cost_usd.median(),
                         exit_shares=f.exit_shares.sum(), residual_shares=f.residual_shares.sum()))
        ledger = g.copy()
        ledger['id'] = np.arange(1, len(ledger) + 1)
        ledger['period'] = 'historical_capture'
        ledger['sport'] = 'baseball'
        ledger['date'] = day
        # Desk payout is total returned cash, including actual modeled exit receipts.
        ledger['payout'] = ledger.payout + ledger.exit_proceeds
        ledger['exit_kind'] = 'target/60s timeout; residual settlement' if policy.startswith('mean') else 'settlement'
        ledger['fee_usd'] = ledger.fee_usd + ledger.exit_fee_usd
        # Exit fees are already netted from proceeds: use gross proceeds for the ledger cash identity.
        ledger['payout'] += ledger.exit_fee_usd
        assert np.allclose(ledger.payout-ledger.stake_usd-ledger.fee_usd, ledger.pnl_usd, equal_nan=True)
        slug = 'live_' + policy
        doc = dict(slug=slug, title=policy.replace('_', ' '), group='Received book studies', sport='baseball', verdict='EXPLORATORY',
                   hypothesis='Replay a fixed rule against depth known on the recorded local receipt clock',
                   entry_rule='One attempt per game; fixed quantity and initial ask+1c limit; delayed displayed-depth crossing',
                   exit_rule='Received bid target or60s timeout+3s, residual settles' if policy.startswith('mean') else 'Verified token resolution',
                   cost_model='Captured market fee rate on entry and any exit;100 inclusive-dollar budget',
                   caveats=['Single recorded day, already inspected', 'Historical tick/minimum constraints and order acceptance unknown',
                            'Displayed depth crossing is a hypothetical sensitivity; queue/rejections and market impact unobserved'],
                   columns=list(ledger), rows=json.loads(ledger.to_json(orient='values')), truncated=False, n_total_trades=len(ledger),
                   report_path='reports/research/LIVE_EXECUTION.md', code_path='pmsports/research/h_live_execution.py')
        target = DATA_DIR / 'research' / 'ledgers' / (slug + '.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix('.json.tmp');temp.write_text(json.dumps(doc, allow_nan=False));temp.replace(target)
    summary = pd.DataFrame(rows) if rows else pd.DataFrame(columns=['policy','observations','fills','capital_usd','resolved_capital_usd','pnl_usd','roi'])
    summary.to_csv(out / 'summary.csv', index=False)
    reasons = audit.groupby(['policy', 'reason'], dropna=False).size().rename('rows').reset_index()
    reasons.to_csv(out / 'reasons.csv', index=False)
    sources.extend(DATA_DIR / 'live' / d / (name+'.jsonl') for d in (previous_day, day) for name in ('mlb','sports'))
    sources.extend([DATA_DIR/'mlb'/'panel.parquet', DATA_DIR/'live'/day/'schedule.parquet'])
    sources.extend(sorted(out.glob('0x*.json')))
    provenance = dict(day=day, messages=n, receipts=len(events), audit_rows=len(audit), metadata_games=len(meta),
                      verified_resolutions=sum(g['resolution_status'].startswith('actual') for g in meta.values()),
                      prior_cells=len(cells), cells_with_30_games=int(cells.games.ge(30).sum()),
                      sources=[dict(path=str(p), size=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns) for p in sources],
                      code={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), ROOT/'pmsports/book_replay.py']})
    (out / 'manifest.json').write_text(json.dumps(provenance, indent=2))
    text = '# Received-book execution study\n\n' + (
        f'Exploratory replay of {day}: {n:,} received book messages across {len(meta)} matched games. '
        'All score increases observed on the MLB and Polymarket sports feeds are retained; the first observation establishes a baseline. '
        'No subsequent price-move filter is used. Primary comparison is3s;1s and5s are declared latency sensitivities. '
        'Only the first score attempt per game/feed can trade. Limit is known initial ask+1c; requested shares are fixed before delay, with a100-dollar fee-inclusive target. '
        'Depth can be partially consumed, is not reused, and must have a received snapshot and an update within5s. '
        'Historical minimum size/tick and actual order acceptance are unobserved, so fills are hypothetical displayed-depth crossings.\n\n'
        'Same-millisecond book messages precede receipt/timer events; sub-millisecond ordering is unavailable. '
        'Pending exits at capture end are identified as censored, with residual settlement kept separate from a tested exit.\n\n'
        'Mean reversion fits market-price mean/std per exact inning/half/outs/bases/lead using prior-season games only, equally weighting each game within a cell. '
        'At an observed MLB state receipt, buy a side below mean minus one standard deviation; at least30 prior games required. '
        'Only one mean signal per game is attempted, after3s. Target is the fixed prior mean; a received bid reaching it submits one3s-delayed limit exit. '
        'After60s any residual submits a3s-delayed aggressive exit; unfilled shares remain through actual contract settlement. '
        'Entry and exit fees are both charged; ROI denominator is entry principal plus all fees, consistent with the desk. No optimization follows the result.\n\n')
    text += summary.to_markdown(index=False, floatfmt='.4f') + '\n\n'
    text += f'Prior cells: {len(cells):,}; cells with at least30 games: {int(cells.games.ge(30).sum()):,}. '
    text += f'Actual token payouts verified for {provenance["verified_resolutions"]}/{len(meta)} games; unresolved positions cannot earn a claimed return.\n\n'
    text += 'Confidence intervals resample games, but one observed day is insufficient to validate a durable edge. Strategy variants share outcomes and are not independent discoveries. '
    text += 'No exchange orders were submitted. Raw audits, reasons, source identities and summaries: data/research/live_execution/' + day + '/.\n\n'
    text += reasons.to_markdown(index=False) + '\n'
    (ROOT / 'reports' / 'research' / 'LIVE_EXECUTION.md').write_text(text)
    print(summary.to_string(index=False));print(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    ap = argparse.ArgumentParser();ap.add_argument('--day', default='2026-09-19')
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    run(args.day)
