"""
PaperFillEngine — simulate market orders against the live order book.

Walks the book to derive VWAP fill price for the requested size, then applies
a fixed taker fee. No network I/O itself — takes a data_client with
`fetch_order_book(symbol, depth)`.
"""

import time
from dataclasses import dataclass


@dataclass
class Fill:
    symbol: str
    side: str           # "buy" | "sell"
    price: float        # VWAP
    qty: float
    usd_cost: float = 0.0       # buy: gross usd (incl fee); sell: 0
    usd_proceeds: float = 0.0   # sell: net usd after fee; buy: 0
    fee_usd: float = 0.0
    timestamp: float = 0.0


class PaperFillEngine:
    def __init__(self, data_client, taker_fee: float = 0.006):
        self.data_client = data_client
        self.taker_fee = taker_fee

    async def simulate_buy(self, symbol: str, usd_amount: float) -> Fill:
        book = await self.data_client.fetch_order_book(symbol, depth=10)
        asks = book.get("asks") or []
        if not asks:
            raise ValueError(f"No asks in order book for {symbol}")

        spendable = usd_amount * (1 - self.taker_fee)
        fee_usd = usd_amount * self.taker_fee

        remaining_usd = spendable
        total_qty = 0.0
        total_cost = 0.0
        for price_s, qty_s in asks:
            price = float(price_s)
            level_qty = float(qty_s)
            level_cost = price * level_qty
            if remaining_usd <= level_cost:
                take_qty = remaining_usd / price
                total_qty += take_qty
                total_cost += take_qty * price
                remaining_usd = 0.0
                break
            total_qty += level_qty
            total_cost += level_cost
            remaining_usd -= level_cost

        vwap = total_cost / total_qty if total_qty > 0 else float(asks[0][0])
        return Fill(
            symbol=symbol,
            side="buy",
            price=vwap,
            qty=total_qty,
            usd_cost=usd_amount,
            fee_usd=fee_usd,
            timestamp=time.time(),
        )

    async def simulate_sell(self, symbol: str, qty: float) -> Fill:
        book = await self.data_client.fetch_order_book(symbol, depth=10)
        bids = book.get("bids") or []
        if not bids:
            raise ValueError(f"No bids in order book for {symbol}")

        remaining_qty = qty
        total_gross = 0.0
        total_filled = 0.0
        for price_s, qty_s in bids:
            price = float(price_s)
            level_qty = float(qty_s)
            take = min(remaining_qty, level_qty)
            total_gross += take * price
            total_filled += take
            remaining_qty -= take
            if remaining_qty <= 0:
                break

        fee_usd = total_gross * self.taker_fee
        proceeds = total_gross - fee_usd
        vwap = total_gross / total_filled if total_filled > 0 else float(bids[0][0])
        return Fill(
            symbol=symbol,
            side="sell",
            price=vwap,
            qty=total_filled,
            usd_proceeds=proceeds,
            fee_usd=fee_usd,
            timestamp=time.time(),
        )
