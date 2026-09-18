"""Small statistical helpers shared by the hypothesis tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..polymarket import taker_fee


def wilson(k: np.ndarray | float, n: np.ndarray | float, z: float = 1.96):
    k, n = np.asarray(k, float), np.asarray(n, float)
    with np.errstate(invalid="ignore", divide="ignore"):
        p = k / n
        den = 1 + z**2 / n
        c = (p + z**2 / (2 * n)) / den
        h = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
    return c - h, c + h


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def log_loss(y, p) -> float:
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y, p) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def bet_pnl(price, won, fee_rate, slip: float = 0.0) -> pd.DataFrame:
    """Per-$1-staked P&L of buying one side as a taker and holding to resolution.

    price  : mid-ish probability of the side bought
    slip   : price paid above `price` (half-spread + impact), in probability points
    fee    : Polymarket taker fee C*rate*p*(1-p), charged in USDC on top
    """
    c = np.clip(np.asarray(price, float) + slip, 0.001, 0.999)
    fee = taker_fee(1.0, c, np.nan_to_num(np.asarray(fee_rate, float)))
    cost = c + fee
    pnl = np.asarray(won, float) - cost
    return pd.DataFrame({"cost": cost, "pnl_per_share": pnl, "roi": pnl / cost})


def bootstrap_mean(x, n_boot: int = 2000, seed: int = 7):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n_boot, len(x)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
