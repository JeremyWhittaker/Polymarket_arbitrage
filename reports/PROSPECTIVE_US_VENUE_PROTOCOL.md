# Prospective paper test on Polymarket US (venue replication)

Specification date: 2026-09-26 UTC. **Paper research only.** The API key used here can read market data and request order *previews*. No code path can create, modify or cancel an order. The account holds no funds.

The international test ([MLB](PROSPECTIVE_PROTOCOL.md), [soccer](PROSPECTIVE_SOCCER_PROTOCOL.md)) observes the international order book. A US trader would use Polymarket US instead. That is a separate CFTC-regulated exchange with its own book, a different contract structure and a higher taker fee. This protocol runs the same frozen triggers against the US book, as a separate test whose results are never pooled with the international test.

## What stays identical

- **MLB states:** the same half-inning-start states from the MLB linescore.
- **MLB fair value:** the same model snapshot (`42fcda2e…`).
- **MLB thresholds:** 10¢ primary and 3¢ control.
- **Soccer triggers:** the same ESPN key-event trigger at 90 minutes or later with a lead; controls in minutes 75–85 and 70–80.
- **Soccer price band:** 0.60–0.97.
- **Sizing:** one attempt per game per policy, $100 maximum including fees, hold to settlement.
- **Endpoints and gates:** the same 500 MLB games or 365 days, and 200 funded soccer games or 183 days; fewer than 100 funded games is inconclusive; the same gate checks.

## Venue-specific rules

| Component | US rule |
|---|---|
| Universe | Games whose Polymarket US event maps before the start to the same game as the international capture: the MLB `aec-` moneyline, or soccer's three `atc-` team/draw markets. Mapping is by event slug plus team identity, with orientation from the US market's team fields. Unmapped games are logged. |
| Contract | An MLB `aec-` market is one instrument. The long side is one team. Buying the other team is a short-side buy, which trades against long-side bids at price 1 − bid. Each soccer `atc-` market's long side is that outcome. |
| Reference price | The last US-venue trade in the leader's side, received strictly before the trigger and no more than 120 seconds old. For the short side, the reference is 1 minus the traded long price. |
| Entry | At trigger receipt plus the same delay as the international test (MLB 0 seconds, soccer 3 seconds), inspect the received US book. Simulate one immediate-or-cancel buy with limit equal to the reference plus 0.01, rounded down to the market tick. Quantity respects the market's minimum and increment. Depth beyond the levels the US stream publishes counts as unavailable. |
| Book freshness | A US book message for that market within 5 seconds of the entry time, on a connection with no disconnect since. |
| Fees | The US taker fee in force at the time. From 2026-09-25 that is 0.0695 × contracts × p × (1 − p). The exchange's preview commission is logged beside it. |
| Preview audit | When the engine processes an entry within 15 seconds of real time, it requests an exchange **order preview** (never an order) for the simulated price and quantity. It logs acceptance or rejection, expected fills, commission and lag. Previews never change simulated cash. Entries replayed later are marked "not previewed". |
| Settlement | The US market's published settlement price. |

## Honesty rules

The rules above were written before any US-venue book or trade data was captured in this project. Activation is recorded in `reports/paper/ACTIVATION_US.json`. Only games scheduled to start after activation count. US soccer coverage is narrower (for example EPL, La Liga, Bundesliga, Serie A, UEFA competitions and MLS), so the soccer endpoint may take longer than the international test's. A US result can confirm or contradict the international test; neither result can be used to choose the other's rules.
