# Historical execution protocol

These repairs produce **causal transaction proxies, not evidence of executable order-book depth or an established edge**. July–September2026 and the strategy variants were already inspected. Regenerating a report does not create a fresh holdout.

## Shared replay contract

`pmsports.execution.TapeReplay(tape).replay(orders, delay_s=3, horizon_s=600, event_cap_usd=100, slip=0)` consumes normalized transaction columns `m,s,ts,q,size,w,fee_rate`. `s` is the acquired outcome: SELL of one binary outcome acquires the complement. Optional `print_id` must be globally unique for the source transaction; repeated explicit IDs are deduplicated. Without it, input row ordinal is the identity, so upstream API overlap must already be deduplicated.

Orders require `m,s,signal_ts`. Optional fields are `receipt_ts`, `delay_s`, `expiry_ts`, `budget_usd`, `target_shares` (alias `requested_shares`), `leader_w`, `event`, `limit_price`, `fee_rate`, and resolution payout `y`. Default budget is$100 inclusive of fees. A known quantity request is fixed before execution; partial paired legs must be retained independently. The engine does not infer equal shares from two future print sizes.

The engine retains every order. Entry is **strictly later than signal, receipt and eligibility**, including delay0 and second-resolution timestamp ties. It rejects invalid times, quantities and budgets, skips the leader's own wallet and wrong acquired side, and ends before expiry. Missing/invalid fee data cannot produce a fee-free fill. Realized void payouts are retained rather than selected away after the fact; historical fees are charged as recorded, with no invented refund.

Each order receives at most one actual relevant print, limited by its remaining shares, the order's budget/quantity and the remaining event cap. A partial does not aggregate future prints at unobserved prices. Orders compete in chronological fill time, then eligibility time and original order order. A transaction's remaining shares can be allocated to another order, but aggregate allocation cannot exceed the original size. Event exposure includes fees and remains occupied until settlement; opposite positions do not free collateral. A fresh replay resets capacity only for a genuinely separate strategy/sensitivity.

Outputs include signal/receipt/eligible/expiry/fill clocks, source print identity, shares, price, observed remaining size, price stake, fees, inclusive capital, payout, net PnL, ROI and `filled`, `partial`, `unfilled`, `ineligible`, `event_cap` or `unknown_fee`. ROI uses **capital including fees**. Unfilled orders use zero shares/capital/PnL and undefined ROI. An exactly completed quantity order is filled even if its dollar budget is not exhausted. Summaries weight by allocated capital and cluster outcomes by game.

## Important clock and venue limitations

The historical Data API settlement timestamp is used as signal receipt when no separately measured receipt exists. No negative estimated settlement-lag adjustment gives the strategy earlier knowledge. Actual public delivery may be later. For MLB, state timestamps are retrospective play clocks; a5s entry delay from that clock is explicitly **optimistic**, particularly given the recovered local feed observation: MLB/free sports updates generally arrived substantially later than book repricing. These simulations do not demonstrate that a free-feed order could arrive at the modeled time.

Next-state expiry is analytical censoring. It is not proof that an already pending sports order can be canceled, nor an exchange-valid short GTD. Actual order lifecycle, sports delay, tick/minimum order size, FAK/FOK rules and book depth require prospective collection. A tiny observed partial is a capacity proxy, not proof a tiny standalone order satisfies the venue minimum. Historical prints never become fabricated asks through a1c spread assumption. Historical tape studies do not have book depth. A separate [received-book experiment](research/LIVE_EXECUTION.md) now replays the Sep19 capture and prior-day initialization; it still cannot prove queue priority or exchange order acceptance. [Polymarket order lifecycle](https://docs.polymarket.com/concepts/order-lifecycle), [placing orders](https://docs.polymarket.com/trading/place-orders), [fees](https://docs.polymarket.com/trading/fees).

## Study-specific choices

- **Thresholds:** cumulative observed notional strictly before a signal, excluding all equal-timestamp prints, must reach$50k. This sensitivity floor matches the legacy collection floor but does not cure incomplete lifetime tapes, missing markets, discovery bias or public receipt uncertainty. The first crossing per market selects a signal; later entry uses the shared replay with a$100 inclusive-dollar event cap. All50 favorite price thresholds and the originally specified favorite/mirror variants have full, uncapped ledger export paths, including empty strategies and no-fills. Fill-price calibration remains descriptive and separate.
- **Whales:** only the previously proposed74/75c primary and73/76c comparator bands, each within0.5c, are evaluated. Leader notional is at least$10k and strictly prior observed market volume at least$50k. Policies are first signal per event and0.1% of observed leader notional capped$100 per order/event. Both have historical fee and+1c sensitivities. The audit retains all signals and their mapping, capital, partial/no-fill status and concentration. No parameter is chosen from a newly inspected winning result.
- **Wallet copying:** delay0 now requires a later other-wallet transaction, rather than buying the leader's own signal retroactively. Equal targets are$100; proportional targets are1% of observed leader notional capped$100. Both share a$100 event cap in each independent replay. Saved cash allocations determine return weights; old price-only caches cannot be used to invent capacity.
- **MLB:** references are last known normalized trades strictly before decision, at most120s old. Home and away have separate actual acquired-side entry candidates. State expiry includes the terminal play; unknown assets cannot provide reference or execution prices. All baseline schedule files supply game-type/innings metadata. H3 and inning rules use shared entry sizing with actual historical fees. Market/model scoring is descriptive; bets settle at resolution. A literal exit at the prior mean with received bid depth, exit fees and a60s time stop is tested separately in [LIVE_EXECUTION.md](research/LIVE_EXECUTION.md).
- **Esports:** `calib_v1.json`, the original round1 coefficients, is required for the primary. Round2 calibration is exploratory because the original holdout had already been inspected. Each signal remains in the audit even with no fill. Entry needs a real later same-side print, capped by its size and$100 inclusive capital; there is no synthetic quote fallback. The module emits the canonical full desk ledger directly. The repaired `ledger_esports_break_overreaction` exporter validates the frozen calibration, clocks and actual cash; it refuses old quote/unit-stake caches. Synthetic exploratory pairs request one share per leg and retain independently partial/failed legs through settlement.

## Rerun and acceptance

This code does not by itself establish profitable results. First rebuild the compact data and MLB baseline/panel using the parent's guarded job. Then run under appropriate memory and deterministic supervision:

```bash
.venv/bin/python -m pmsports calibration
.venv/bin/python -m pmsports calibration --points
.venv/bin/python -m pmsports.analysis.whale_prices
.venv/bin/python -m pmsports thresholds
.venv/bin/python -m pmsports.research.h_esports_break_overreaction analyze --holdout --rebuild
```

The remaining commands are `.venv/bin/python -m pmsports wallets-report` and `.venv/bin/python -m pmsports report --split-date 2026-01-01`; their existing versioned wallet cache must include `execution.py` in source identity before reuse. Parent validation must reconcile saved ledgers with allocated cash and every no-fill, review uncertainty/concentration and distinguish negative evidence from unavailable inputs. No live trades are authorized by this repair.
