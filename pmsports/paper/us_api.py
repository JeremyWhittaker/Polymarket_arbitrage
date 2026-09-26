"""Polymarket US API access for the US-venue paper test: public market data, signed reads and
order *previews*. There is deliberately no function that creates, modifies or cancels orders.

Credentials come from the repo's git-ignored `.env` (POLYMARKET_US_KEY_ID, POLYMARKET_US_SECRET_KEY,
POLYMARKET_US_API). Requests are signed with Ed25519 over f"{timestamp_ms}{METHOD}{path}".
"""
from __future__ import annotations

import base64
import json
import os
import time
from functools import lru_cache
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.polymarket.us"
GATEWAY = "https://gateway.polymarket.us"
WS_MARKETS = "wss://api.polymarket.us/v1/ws/markets"
PREVIEW_PATH = "/v1/order/preview"
# Paths this module may sign. Anything that could place, modify or cancel an order is absent on purpose.
SIGNED_PATHS = ("/v1/account/balances", "/v1/portfolio/positions", "/v1/orders/open", PREVIEW_PATH, "/v1/ws/markets")


@lru_cache(maxsize=1)
def credentials() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith("POLYMARKET_US_")})
    if not env.get("POLYMARKET_US_KEY_ID") or not env.get("POLYMARKET_US_SECRET_KEY"):
        raise RuntimeError("Polymarket US credentials missing from .env")
    return env


@lru_cache(maxsize=1)
def _key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(credentials()["POLYMARKET_US_SECRET_KEY"])[:32])


def signed_headers(method: str, path: str) -> dict:
    if path.split("?")[0] not in SIGNED_PATHS:
        raise ValueError(f"refusing to sign {method} {path}: not an allowed read/preview path")
    ts = str(int(time.time() * 1000))
    sig = base64.b64encode(_key().sign(f"{ts}{method.upper()}{path}".encode())).decode()
    return {"X-PM-Access-Key": credentials()["POLYMARKET_US_KEY_ID"], "X-PM-Timestamp": ts, "X-PM-Signature": sig}


def get_signed(path: str, timeout: float = 20) -> dict:
    r = requests.get(API + path, headers=signed_headers("GET", path), timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_public(path: str, params: dict | None = None, timeout: float = 20) -> dict:
    r = requests.get(GATEWAY + path, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def preview_buy(market_slug: str, price: float, quantity: int, intent: str = "ORDER_INTENT_BUY_LONG",
                timeout: float = 10) -> tuple[int, dict | str]:
    """Ask the exchange to *preview* an immediate-or-cancel limit buy. Never submits an order."""
    body = {"request": {"marketSlug": market_slug, "type": "ORDER_TYPE_LIMIT",
                        "price": {"value": f"{price:.4f}", "currency": "USD"}, "quantity": int(quantity),
                        "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", "intent": intent,
                        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}}
    h = signed_headers("POST", PREVIEW_PATH) | {"Content-Type": "application/json"}
    r = requests.post(API + PREVIEW_PATH, headers=h, data=json.dumps(body), timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:2000]


def ws_headers() -> dict:
    return signed_headers("GET", "/v1/ws/markets")
