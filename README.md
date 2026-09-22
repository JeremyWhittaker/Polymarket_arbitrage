# Polymarket sports research

This project investigates sports-feed timing, state-based prices, favorite calibration,
and copying informed wallets. It is a research tool; no real-money orders are placed.

**No executable trading edge has been established.** Repairs and reruns are in progress.
The [independent review](reports/PEER_REVIEW.md) maps Jeremy's original prompts to the
implementation. The [repair checklist](reports/REPAIR_CHECKLIST.md) and
[validation evidence](reports/REPAIR_VALIDATION.md) track what has actually been verified.
Earlier strategy reports and desk returns remain exploratory until their corrected rerun
is identified there. July–September 2026 has already been inspected and is not a fresh
confirmatory holdout. Dollar weighting is legitimate when the weights and available
capital are known at decision time; disagreement with equal weighting alone proves no artifact.

A recovered September 19 live capture contains 61 scoring events across 14 games with a
subsequent price move of at least three cents. Median book half-move time was 2.86 seconds
after the retrospective contact clock, versus 41.10 seconds for the MLB feed and 71.41
seconds for the Polymarket sports feed. This selected sample weighs against reacting to
these free feeds before the market moves; it does not measure a trading return or all events.

## Data and execution rules

- New collection defaults to **no eventual-volume floor**. `--min-volume` is an explicitly
  retrospective restriction. The old corpus selected eventual volume of at least $50k;
  imposing $25k observed before a decision does not recover missing markets.
- Tape upgrades request market creation through closure plus 30 minutes. Missing creation
  metadata triggers a market lookup; otherwise coverage is explicitly bounded from `--since`
  (default January 1, 2025). Delayed games are not clipped at 14 hours. Per-tape manifests bind
  requested windows, collector version, retrieval status and output identity. API-returned
  history still does not prove provider completeness or available order-book depth.
- Pregame decisions occur ten minutes before scheduled start. Reference price and liquidity
  use only earlier prints. The entry proxy is the first same-side print more than five seconds
  later and before start. Its size caps a $100 target including fees; unfilled signals remain
  counted. Legacy `ask` columns hold these proxies, not historical quotes.
- Shared research rebuilds preserve existing integer market/wallet IDs, including missing or
  empty former tapes. New IDs append. Code and input identities accompany each generation;
  an interrupted promotion fails closed. Legacy caches remain readable with an explicit audit
  warning. Do not run cache writers concurrently with research readers.
- Historical taker prints alone cannot establish a follower's executable fill, queue position,
  simultaneous multi-leg prices, or cancellation success during a sports delay. Fee assumptions
  must use each market's metadata. Current venue rules are documented in
  [fees](https://docs.polymarket.com/trading/fees) and
  [order lifecycle](https://docs.polymarket.com/concepts/order-lifecycle).
- The [shared execution protocol](reports/EXECUTION_PROTOCOL.md) requires a strictly later
  acquired-side print, excludes a copied leader's own transactions, consumes each print's
  shares once, and retains partial fills and no-fills. Actual allocated capital includes fees.
  The rebuilt compact cache contains 53,771,660 valid fills across 38,370 markets; existing
  market IDs are preserved. This remains a selected historical sample.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pmsports --help
.venv/bin/python -m pmsports games
.venv/bin/python -m pmsports fetch
.venv/bin/python -m pmsports panel
.venv/bin/python -m pmsports live-latency --day 2026-09-19
.venv/bin/python -m pmsports universe
.venv/bin/python -m pmsports tapes --max-markets 100 --since 2025-01-01
.venv/bin/python -m pmsports favorites
.venv/bin/python -m pmsports.research.common build
.venv/bin/python -m pmsports wallets-report
.venv/bin/python -m pmsports.webapp
.venv/bin/python -m pytest -q tests
```

Data and raw live captures stay in ignored `data/`. Full collection/rebuilds are substantial
jobs; choose explicit limits and monitor resource usage. Failed tape upgrades preserve the
previous valid file. Versioned caches invalidate when inputs or dependent code change.
The research desk uses port 8808 and supports sport/period filters, paginated complete
ledgers, and noindex/robots exclusion. Build/startup alone is not browser or strategy validation.

## Sources

Gamma supplies market metadata; the Data API supplies attributed taker prints; CLOB supplies
price history and live order-book updates; MLB Stats API supplies play-by-play and schedules.
The local historical corpus has no complete order-book archive. Third-party archives may
exist, but access and coverage have not been verified for these studies. The original 2024
election scripts remain unmaintained under `legacy/election_2024/`.

## Layout

```
pmsports/
  http.py          rate-limited, retrying HTTP (per-host token bucket)
  polymarket.py    Gamma discovery, CLOB price history, Data-API trades, fee formula
  mlb.py           MLB schedule + timestamped plate appearances
  collect.py       games table (PM <-> MLB join), resumable per-game fetch
  panel.py         state x price panel, pregame table, MLB-only baseline
  record.py        live websocket recorder (books, PM scores, MLB linescore)
  analysis/
    hypotheses.py  H1 calibration, H2 state tables, H3 out-of-sample backtest, H4 latency
    report.py      renders reports/REPORT.md + CSV + charts
    live.py        per-scoring-play latency from a recorded day
    favorites.py   pregame-favorite (and underdog) hold-to-resolution backtest, all sports
    thresholds.py  price-threshold grid by sport; MLB leader-below-historical-rate rule
    calibration.py price vs realized win rate by sport, and the tradable threshold rules
  wallets/
    universe.py    all resolved sports game markets (Gamma tag 100639), sport family, payouts
    tapes.py       per-market taker tapes; compact categorical loader; budget memory for the full corpus
    skill.py       positions, luck-adjusted z, FDR, copy-trade execution proxies, clustered bootstrap
    study.py       selection rules vs placebo, walk-forward, big-trade signal, skilled decomposition
    onchain.py     CryptoHouse queries (maker/taker profile per wallet)
    report.py      renders reports/WALLETS.md
tests/             unit tests (fee formula, orientation fix, state machine, P&L math)
legacy/            2024 election-arbitrage scripts (unmaintained)
```
