"""Shared helpers for Sub-project 4 attribution scripts.

All 5 SP4 scripts import from this module. Dashboard endpoints (Task 7)
reuse the same logic.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import requests


PROD_BASE_URL = "https://gracious-inspiration-production.up.railway.app"

# Trades with buy time BEFORE this cutoff are excluded from attribution.
# Set to the post-restoration deploy timestamp (commit f287d14, 2026-05-23 05:09 UTC)
# so the stale-cache zombies + their cleanup losses don't pollute reports.
# To include all history, set to "" (empty string).
MIN_TRADE_TIMESTAMP = "2026-05-23T15:40:00+00:00"


@dataclass
class PairedTrade:
    """A buy matched with all its sells (TP1 partial + TP2 partial + ...)."""
    bot_id: str
    token: str
    entry_price: float
    size_usd: float
    realized_pnl_usd: float
    time: str
    sells: list[dict] = field(default_factory=list)
    buy_meta: dict = field(default_factory=dict)


@dataclass
class BotMetrics:
    bot_id: str
    sample_n: int
    total_pnl_usd: float
    pnl_per_trade: Optional[float]
    win_rate: Optional[float]
    avg_win_usd: Optional[float]
    avg_loss_usd: Optional[float]
    best_trade_usd: float
    worst_trade_usd: float
    throughput_x_pnl: float


def fetch_all_trades(base_url: str = PROD_BASE_URL, limit: int = 2000) -> list[dict]:
    """Pull all trades from production with full entry_meta."""
    resp = requests.get(f"{base_url}/api/trades", params={"full": "1", "limit": limit})
    resp.raise_for_status()
    return resp.json()


def pair_buys_sells(trades: list[dict]) -> list[PairedTrade]:
    """Match buys with their sells by (bot_id, token, entry_price).

    Multiple sells per buy (TP1 partial + TP2 partial) are aggregated:
    realized_pnl_usd = sum of all sell records' pnl for that key.

    Unpaired buys (open positions) are excluded.

    When MIN_TRADE_TIMESTAMP is non-empty, paired trades whose BUY time is
    before the cutoff are dropped — both the buy and any matched sells.
    """
    buys_by_key: dict[tuple, dict] = {}
    sells_by_key: dict[tuple, list[dict]] = defaultdict(list)
    for t in trades:
        bid = t.get("bot_id", "baseline_v1")
        token = t.get("token")
        price = t.get("entry_price")
        if price is None:
            continue
        key = (bid, token, price)
        if t.get("type") == "buy":
            buys_by_key[key] = t
        elif t.get("type") == "sell":
            sells_by_key[key].append(t)

    paired: list[PairedTrade] = []
    for key, buy in buys_by_key.items():
        sells = sells_by_key.get(key, [])
        if not sells:
            continue
        if MIN_TRADE_TIMESTAMP and (buy.get("time") or "") < MIN_TRADE_TIMESTAMP:
            continue
        total_pnl = sum(s.get("pnl", 0.0) for s in sells)
        paired.append(PairedTrade(
            bot_id=key[0],
            token=key[1],
            entry_price=key[2],
            size_usd=float(buy.get("amount_usd", 0.0)),
            realized_pnl_usd=total_pnl,
            time=buy.get("time", ""),
            sells=sells,
            buy_meta=buy.get("entry_meta") or {},
        ))
    return paired


def compute_metrics(paired: list[PairedTrade]) -> BotMetrics:
    """Compute summary metrics for one bot's paired trades."""
    bot_id = paired[0].bot_id if paired else "?"
    n = len(paired)
    if n == 0:
        return BotMetrics(
            bot_id=bot_id, sample_n=0, total_pnl_usd=0.0,
            pnl_per_trade=None, win_rate=None,
            avg_win_usd=None, avg_loss_usd=None,
            best_trade_usd=0.0, worst_trade_usd=0.0,
            throughput_x_pnl=0.0,
        )
    pnls = [p.realized_pnl_usd for p in paired]
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    per_trade = total / n
    return BotMetrics(
        bot_id=bot_id,
        sample_n=n,
        total_pnl_usd=total,
        pnl_per_trade=per_trade,
        win_rate=len(wins) / n,
        avg_win_usd=(sum(wins) / len(wins)) if wins else None,
        avg_loss_usd=(sum(losses) / len(losses)) if losses else None,
        best_trade_usd=max(pnls),
        worst_trade_usd=min(pnls),
        throughput_x_pnl=n * per_trade,
    )


def confidence_label(n: int) -> str:
    """Sample-size confidence indicator."""
    if n < 5:
        return "Very low (n<5)"
    if n < 20:
        return "Low (n<20)"
    return "OK"
