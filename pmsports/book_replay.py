"""Received-depth crossing sensitivity, without claims about exchange acceptance/queue.

Each policy is an independent replay; consumed levels cannot be reused by that
policy until incremental displayed quantity replenishes them. Receipt time alone
orders the simulation. Exchange timestamps never grant earlier knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from .polymarket import taker_fee


@dataclass
class Level:
    reported: float
    used: dict = field(default_factory=dict)

    def update(self, size):
        self.reported = size
        self.used = {key: min(value, size) for key, value in self.used.items()}

    def available(self, policy):
        return max(0., self.reported - self.used.get(policy, 0.))


class ReceivedBook:
    def __init__(self):
        self.books = {}
        self.last_ms = {}
        self.initialized = set()
        self.last_input_ms = -math.inf

    def _level(self, token, side, price, size):
        if not (math.isfinite(price) and math.isfinite(size) and 0 < price < 1 and size >= 0):
            return False
        levels = self.books.setdefault(token, {"BUY": {}, "SELL": {}})[side]
        if size == 0:
            levels.pop(price, None)
        elif price in levels:
            levels[price].update(size)
        else:
            levels[price] = Level(size)
        return True

    def apply(self, recv_ms, message):
        if not math.isfinite(recv_ms):
            raise ValueError("nonfinite receipt time")
        if recv_ms < self.last_input_ms:
            raise ValueError("received book messages are out of order")
        self.last_input_ms = recv_ms
        messages = message if isinstance(message, list) else [message]
        changed = set()
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            kind = msg.get("event_type")
            if kind == "book":
                token = str(msg["asset_id"])
                book = self.books.setdefault(token, {"BUY": {}, "SELL": {}})
                for side, name in (("BUY", "bids"), ("SELL", "asks")):
                    levels = {float(x["price"]): float(x["size"]) for x in msg.get(name, [])}
                    if any(not math.isfinite(p) or not math.isfinite(v) or v < 0 for p, v in levels.items()):
                        raise ValueError("invalid snapshot depth")
                    for price in list(book[side]):
                        if price not in levels:
                            del book[side][price]
                    for price, size in levels.items():
                        self._level(token, side, price, size)
                self.initialized.add(token)
                changed.add(token)
            elif kind == "price_change":
                for x in msg.get("price_changes", []):
                    token = str(x["asset_id"])
                    if x.get("side") not in ("BUY", "SELL"):
                        continue
                    if self._level(token, x["side"], float(x["price"]), float(x["size"])):
                        changed.add(token)
        for token in changed:
            self.last_ms[token] = recv_ms
        return changed

    def quote(self, token, buy=True, policy="default"):
        book = self.books.get(str(token), {})
        side = "SELL" if buy else "BUY"
        prices = [p for p, level in book.get(side, {}).items() if level.available(policy) > 1e-10]
        return (min(prices) if buy else max(prices)) if prices else math.nan

    def cross(self, token, now_ms, *, policy="default", buy=True, budget=100.,
              shares=math.inf, limit=None, fee_rate=.05, stale_ms=5000,
              min_order_shares=None):
        """Cross currently received levels once; FAK-like partials, inclusive budget.

        The caller schedules eligibility and fixes its limit before this call.
        Minimum quantity can be supplied only when known at that historical time.
        Unknown constraints are retained as a qualification, not proof of validity.
        """
        result = dict(shares=0., gross_usd=0., fee_usd=0., cash_usd=0.,
                      price=math.nan, status="unfilled", reason="", book_ms=self.last_ms.get(str(token)),
                      constraint_status="known minimum" if min_order_shares is not None else "historical order constraints unknown")
        token = str(token)
        if not (math.isfinite(now_ms) and math.isfinite(fee_rate) and fee_rate >= 0 and
                math.isfinite(budget) and budget > 0 and shares > 0 and not math.isnan(shares)):
            raise ValueError("invalid order")
        if not math.isfinite(stale_ms) or stale_ms < 0 or (not buy and not math.isfinite(shares)):
            raise ValueError("invalid staleness or sell quantity")
        if min_order_shares is not None and (not math.isfinite(min_order_shares) or min_order_shares < 0):
            raise ValueError("invalid minimum order size")
        if limit is None or not math.isfinite(limit) or not 0 < limit < 1:
            result.update(reason="invalid_limit")
            return result
        if token not in self.initialized:
            result.update(reason="no_initial_snapshot")
            return result
        age = now_ms - self.last_ms[token]
        if age < 0 or age > stale_ms:
            result.update(reason="stale_or_future_book")
            return result
        raw = self.books[token]
        bid = max(raw['BUY'], default=math.nan)
        ask = min(raw['SELL'], default=math.nan)
        if math.isfinite(bid) and math.isfinite(ask) and bid >= ask:
            result.update(reason="crossed_book")
            return result
        requested = min(shares, budget / (limit + float(taker_fee(1., limit, fee_rate)))) if buy else shares
        if min_order_shares is not None and requested < min_order_shares:
            result.update(reason="below_minimum_order")
            return result
        levels = self.books[token]["SELL" if buy else "BUY"]
        for price in sorted(levels, reverse=not buy):
            if (buy and price > limit) or (not buy and price < limit):
                break
            fee1 = float(taker_fee(1., price, fee_rate))
            quantity = min(levels[price].available(policy), requested - result["shares"])
            if buy:
                quantity = min(quantity, max(0., budget - result["cash_usd"]) / (price + fee1))
            if quantity <= 1e-10:
                continue
            levels[price].used[policy] = levels[price].used.get(policy, 0.) + quantity
            result["shares"] += quantity
            result["gross_usd"] += price * quantity
            result["fee_usd"] += fee1 * quantity
            result["cash_usd"] = result["gross_usd"] + (1 if buy else -1) * result["fee_usd"]
        if result["shares"]:
            result["price"] = result["gross_usd"] / result["shares"]
            result["status"] = "filled" if result["shares"] >= requested - 1e-8 else "partial"
        else:
            result["reason"] = "no_depth_at_limit"
        return result
