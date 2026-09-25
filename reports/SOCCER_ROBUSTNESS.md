# Soccer controls and robustness after literal BUY/SELL repair

Accepted historical data, 2026-09-23 UTC. The full replay covered 4,739 games and 106,519 events, with 38 declared configurations and two cost cases. All 152 summary cells reconcile; 50,491 entry/exit records trace to raw native action, token, price and size. The full candidate audit has 782,633 rows and the executed-policy audit has 37,948 trade legs. [Full five-family report](research/SOCCER_CONTINUATION.md).

**Added-time leaders are a credible exploratory lead, not a confirmed executable edge.** The +1¢ evaluation result survives concentration checks and both prespecified controls, but its development return is negative and longer entry delays weaken it materially. Soccer has no captured historical order books or measured feed receipts. These periods were already inspected; all intervals are nominal and unadjusted for correlated hypothesis/variant searches. Production strategy and matched-control intervals use 1,000 whole-game resamples, seed 20260922. The original Dutch one-goal diagnostic below uses shared `C.cluster_ci` defaults: 2,000 resamples, seed 0; these settings are disclosed rather than selected by the sign of an endpoint.

## Added-time leader: declared +1¢ case

The primary ledger uses the extra-cent case: 186 selected evaluation signals, 180 funded games, 121 partial fills and six no-fills. The fee-only table has 182 funded signals; its sample must not be attached to the +1¢ return. Evaluation signal dates are July 1–September 18, 2026; development signal dates are October 9, 2025–June 30, 2026. All dates are UTC.

| Period | Funded games | Capital including fees | Net P&L | Cash ROI | Equal-game ROI | Median funded allocation |
|---|---:|---:|---:|---:|---:|---:|
| dev | 450 | $18,726.95 | $-49.29 | -0.26% | -0.87% | $20.21 |
| holdout | 180 | $8,095.95 | $469.87 | +5.80% | +4.43% | $25.28 |

Evaluation cash ROI has a nominal 95% game-cluster interval of **+1.22% to +9.32%**. Only 59 fills reach the full $100 target; 46 are below $5. These are later-print allocations, not demonstrated accessible liquidity. Capital sums repeated historical allocations, not required account equity.

| Evaluation concentration check | Games removed | Remaining capital | Remaining P&L | Cash ROI |
|---|---:|---:|---:|---:|
| Best 1 | 1 | $7,995.95 | $420.89 | +5.26% |
| Best 3 | 3 | $7,807.20 | $364.17 | +4.66% |
| Best 1% | 2 | $7,895.95 | $392.42 | +4.97% |

Removal ranks whole funded games by dollar P&L, while preserving the original no-fill audit. Top 1% means ceiling(1% × funded games), or two games here. These are descriptive sensitivity checks, not a new rule or adjusted significance test.

| Entry delay after decision | Evaluation funded | Cash ROI after +1¢ | Nominal 95% interval |
|---|---:|---:|---|
| 3 seconds | 180 | +5.80% | +1.22% to +9.32% |
| 10 seconds | 178 | +1.74% | -3.25% to +5.98% |
| 60 seconds | 165 | +1.02% | -4.57% to +5.76% |

The 10- and 60-second delays replace the primary 3-second delay; they are not additional delays. Fill populations can change. Even these clocks begin from retrospective historical decisions rather than measured local receipt. The favorable 3-second result cannot be assumed obtainable through a public feed.

| Price-matched evaluation control | Leader ROI | Reweighted control ROI | Difference, percentage points | Nominal difference interval, percentage points |
|---|---:|---:|---:|---|
| 75–85 minutes | +5.80% | -7.08% | +12.89 | +2.57 to +24.09 |
| 70–80 minutes | +5.80% | -5.76% | +11.56 | +1.63 to +22.04 |

Both comparisons retain all leader decision-price bins; approximately 80.2% and 76.2% of the corresponding control signals share that support. A joint game bootstrap preserves overlap between the arms. Reweighted controls describe a comparison, not extra capacity or an independently profitable portfolio. Development differences have intervals crossing zero. The existing frozen [MLB paper protocol](PROSPECTIVE_PROTOCOL.md) remains unchanged; this audit does not activate a soccer experiment.

## Original Dutch-book one-goal-per-game inference

The production policy attempts each selected goal subject to the shared game cap and clusters all goals by game. Its evaluation contains 27 funded goals in 25 games and returns −2.15%. The original prompt also said one goal per game for inference. The following diagnostic makes that literal without choosing winners or funded entries: globally select each game’s earliest selected goal by decision time, before examining fills/outcomes; break exact time ties by signal ID. Retain every leg, partial fill, failed unwind, residual settlement and no-fill of that goal. This is an explicitly declared selection convention, not a newly optimized strategy.

| Period / cost case | Selected goals / games | Funded legs | Capital | Net P&L | Cash ROI / 95% interval | Equal-game ROI / 95% interval |
|---|---:|---:|---:|---:|---|---|
| dev/0.0 | 148 | 424 | $1,778.33 | $-154.00 | -8.66% / -19.04% to +0.23% | -8.23% / -15.93% to +0.81% |
| dev/0.01 | 148 | 423 | $1,700.67 | $-227.40 | -13.37% / -23.85% to -4.32% | -14.01% / -21.52% to -5.83% |
| holdout/0.0 | 25 | 72 | $628.24 | $-15.79 | -2.51% / -11.85% to +3.37% | -5.40% / -12.41% to +0.30% |
| holdout/0.01 | 25 | 71 | $533.54 | $-34.46 | -6.46% / -18.36% to +0.30% | -14.08% / -23.97% to -6.42% |

Cost case `0.0` includes fees; `0.01` adds one cent of adverse price per share. Independent checks found no earliest-decision ties and no later selected entry window overlapping the first goal’s entry window. All three submitted legs per chosen goal are retained. The first selected goal has no earlier submitted goal consuming the policy’s game budget, so its existing fill/exit records can be retained without replaying later goals. Neither the original one-goal inference nor the complete multi-goal policy demonstrates a profitable lock. Cash and equal-game intervals both resample whole games with 2,000 draws and seed 0.

## Other mechanism checks

Early red-card opponent trades return +20.96% in evaluation with an interval crossing zero. The declared late-card, card-side branch instead returns −55.70% across 22 funded signals (interval −100.00% to +229.35%; +1¢ return −65.95%). It does not support the proposed early/late timescale mechanism. The early branch remains an uncertain directional lead, separately from that mechanistic claim.

Substitution coherence passes the declared approximate 1¢ point tolerance, not an exact-zero or equivalence test. Its positive return has very wide uncertainty and turns into a loss after removing the best three games. The draw-anchor age/count model explains little; that does not establish that the draw price is correct or eliminate all staleness explanations.

## Reproduction from accepted full artifacts

Run this from the repository root with `.venv/bin/python` and `PYTHONPATH=.`. It reads the complete corrected signal/trade artifacts and changes no data. The full run itself is reproduced with `.venv/bin/python -m pmsports.research.h_soccer_continuation`; the report above also uses its saved matched-control results.

```python
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from pmsports.research.common import cluster_ci

root = Path('data/research/soccer_continuation')
a = pd.read_parquet(root / 'signals.parquet', columns=['family','variant','selected','event','signal_id','signal_ts','period'])
t = pd.read_parquet(root / 'trades.parquet')
r = json.loads((root / 'results.json').read_text())
assert r['run']['action_policy'] == 'soccer-direct-buy-sell-v2'

def summary(rows, selected):
    f = rows[rows.cost_usd.gt(0)]
    g = f.groupby('event', sort=True).agg(pnl=('pnl_usd','sum'), capital=('cost_usd','sum'))
    cash = cluster_ci(g.pnl/g.capital, g.index, weights=g.capital) if len(g) else (None,)*3
    equal = cluster_ci(g.pnl/g.capital, g.index) if len(g) else (None,)*3
    return dict(selected_goals=selected, funded_signals=f.signal_id.nunique(), funded_games=len(g),
                funded_legs=len(f), capital_usd=f.cost_usd.sum(), pnl_usd=f.pnl_usd.sum(),
                cash_roi_ci=cash, equal_game_roi_ci=equal)

out = {'dutch_first_selected': {}, 'leader_plus_1c': {}}
# Select before inspecting fills or outcomes, globally across both periods.
d = a[a.family.eq('dutch') & a.variant.eq('primary') & a.selected]
first = d.sort_values(['signal_ts','signal_id'], kind='stable').drop_duplicates('event')
for period in ('dev','holdout'):
    chosen = first[first.period.eq(period)]
    for slip in (0., .01):
        rows = t[t.family.eq('dutch') & t.variant.eq('primary') & t.slip.eq(slip) & t.signal_id.isin(chosen.signal_id)]
        out['dutch_first_selected'][f'{period}/{slip}'] = summary(rows, len(chosen))
    rows = t[t.family.eq('leader') & t.variant.eq('primary') & t.slip.eq(.01) & t.period.eq(period)]
    g = rows[rows.cost_usd.gt(0)].groupby('event').agg(pnl=('pnl_usd','sum'), capital=('cost_usd','sum'))
    ranked = g.sort_values(['pnl'], ascending=False, kind='stable')
    z = dict(capital_usd=g.capital.sum(), pnl_usd=g.pnl.sum(), cash_roi=g.pnl.sum()/g.capital.sum(),
             equal_game_roi=(g.pnl/g.capital).mean(), funded_games=len(g), median_capital=g.capital.median())
    z['drop_best'] = {}
    for label, n in [('1',1),('3',3),('top1pct',math.ceil(.01*len(g)))]:
        left = ranked.iloc[n:]
        z['drop_best'][label] = dict(removed=n, capital_usd=left.capital.sum(), pnl_usd=left.pnl.sum(), roi=left.pnl.sum()/left.capital.sum())
    z['delay_cases'] = {str(delay): r['results'][f'leader/delay_{delay}'][period]['0.01'] for delay in (10,60)}
    out['leader_plus_1c'][period] = z

def clean(x):
    if isinstance(x,dict): return {k:clean(v) for k,v in x.items()}
    if isinstance(x,(tuple,list)): return [clean(v) for v in x]
    if isinstance(x,np.integer): return int(x)
    if isinstance(x,(float,np.floating)): return float(x) if np.isfinite(x) else None
    return x
print(json.dumps(clean(out), indent=2, allow_nan=False))
```

## Additional concentration and hedge checks

These figures were recomputed from the accepted `data/research/soccer_continuation/trades.parquet` (primary variant, July-onward evaluation). They group funded legs by game and remove the games with the largest net P&L. They add no new rule or interval.

| Rule / cost case | Funded games | Capital | Net P&L | Cash ROI | After removing best three games |
|---|---:|---:|---:|---:|---|
| Early red-card opponent, fee only | 61 | $2,519.92 | +$528.11 | +20.96% | +$103.62 on $2,219.92 (+4.67%) |
| Early red-card opponent, +1¢ | 61 | $2,530.51 | +$477.85 | +18.88% | +$70.88 on $2,230.51 (+3.18%) |
| Early substitution opponent, fee only | 47 | $1,474.53 | +$396.47 | +26.89% | −$244.21 on $1,272.33 (−19.19%) |
| Draw-anchor team pair, fee only | 52 | $391.15 | +$18.52 | +4.73% | −$23.04 on $329.94 (−6.98%) |

Every draw-anchor signal is a two-leg pair. Of the 52 funded evaluation signals, 47 have a failed or partial hedge and only 5 have both legs completely filled.

Independent operational evidence: `soccer-direct-acceptance.json`, `soccer-supplement-parent.json`, and `soccer-final-review.md/json` under `.foreman/repair-20260922/`. Full JSON/Parquet data remain local under ignored `data/`. No real-money orders were placed.
