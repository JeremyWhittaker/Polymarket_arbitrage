"""On-chain fills via CryptoHouse (ClickHouse's free public SQL endpoint over Polymarket data).

  https://crypto-clickhouse.clickhouse.com  user `crypto`, no password, read-only.
  Tables: polymarket.orders_filled (257M CTF-exchange OrderFilled events, 2022-11 .. 2026-01-05,
  i.e. up to the V2 exchange migration), user_positions (subgraph realized P&L), assets, ...

Hard limits on the public user: 2,000 result rows, 1 MB result, 60 s per query, settings
read-only. So it is used for *aggregates* (wallet profiles), never bulk downloads.

Fill semantics (verified): every order appears exactly once as `maker` of an OrderFilled
event; when the order was the taker, the event's `taker` is the exchange contract.
maker_asset_id == '0' means the order paid USDC (a BUY); amounts are 6-decimal units.
"""
from __future__ import annotations

import io
import logging

import pandas as pd
import requests

log = logging.getLogger("pmsports")
CH = "https://crypto-clickhouse.clickhouse.com/"
EXCHANGES = ("0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",   # CTF Exchange (v1)
             "0xc5d563a36ae78145c45a50134d48a1215220f80a")   # Neg Risk CTF Exchange (v1)


def query(sql: str, ext: dict[str, tuple[str, list[str]]] | None = None, timeout: int = 90) -> pd.DataFrame:
    """Run SQL; `ext` uploads external tables: {name: ("col Type", [rows...])}."""
    params, files = {"query": sql + " FORMAT TSVWithNames"}, None
    if ext:
        files = {}
        for name, (structure, rows) in ext.items():
            params[f"{name}_structure"] = structure
            files[name] = (name, "\n".join(rows).encode())
    r = requests.post(CH, params=params, files=files, auth=("crypto", ""), timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"CryptoHouse {r.status_code}: {r.text[:300]}")
    return pd.read_csv(io.StringIO(r.text), sep="\t")


def wallet_profiles(wallets: list[str], months: list[str], chunk: int = 1500) -> pd.DataFrame:
    """Per wallet, all markets: USD volume, share of USD traded as taker, fill count.

    Run per month (60 s cap) and per wallet chunk (2,000-row cap); sums are additive.
    """
    parts = []
    exch = ",".join(f"'{e}'" for e in EXCHANGES)
    for m0, m1 in zip(months[:-1], months[1:]):
        for i in range(0, len(wallets), chunk):
            w = [x.lower() for x in wallets[i:i + chunk]]
            sql = f"""
            SELECT maker AS wallet,
                   sum(if(maker_asset_id = '0', maker_amount_filled, taker_amount_filled)) / 1e6 AS usd,
                   sumIf(if(maker_asset_id = '0', maker_amount_filled, taker_amount_filled),
                         taker IN ({exch})) / 1e6 AS usd_taker,
                   count() AS fills
            FROM polymarket.orders_filled
            WHERE timestamp >= '{m0}' AND timestamp < '{m1}' AND maker IN (SELECT w FROM wl)
            GROUP BY maker"""
            parts.append(query(sql, {"wl": ("w String", w)}))
    df = pd.concat(parts).groupby("wallet").sum()
    df["taker_share"] = df.usd_taker / df.usd
    return df
