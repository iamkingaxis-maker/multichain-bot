"""
ScalpQueue — high-volume scalp strategy feeder.

Two inputs:
  A. DexScreener REST scan every 90s (quality-gated candidates)
  B. (future) Axiom trending events

Candidates enter a watch set (max 25, 30-min expiry).
Axiom tick gate fires entry when:
  1. 3+ consecutive upticks in last 15s
  2. Buy/sell ratio > 0.65 over last 30s
  3. Positive tick trend over 30s
  4. Price movement since watch entry <= 3%
  5. ScalpCapitalManager has capacity
"""

import asyncio
import logging
import time
import aiohttp
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_SCAN_INTERVAL = 90  # seconds
_DEX_CHAIN = "solana"
_SEARCH_TERMS = ["sol", "bonk", "wif", "cat", "dog", "meme", "pepe", "ai", "pump", "baby"]


class ScalpQueue:
    def __init__(self,
                 trader,
                 axiom_price_feed,
                 open_positions_ref: dict,
                 scalp_capital,
                 config):
        self.trader = trader
        self.axiom_price_feed = axiom_price_feed
        self.open_positions_ref = open_positions_ref
        self.scalp_capital = scalp_capital
        self.cfg = config

        # addr -> {"symbol", "entry_price", "entry_ts"}
        self._watch: Dict[str, dict] = {}
        # addr -> monotonic expiry timestamp
        self._stop_cooldowns: Dict[str, float] = {}

    async def run(self):
        logger.info("[ScalpQueue] Starting — watching for scalp entries")
        while True:
            try:
                await self._scan_cycle()
                self._prune_watch_set()
            except Exception as e:
                logger.error(f"[ScalpQueue] Error: {e}")
            await asyncio.sleep(_SCAN_INTERVAL)

    def on_scalp_close(self, addr: str, reason: str, pnl_usd: float = 0.0):
        """Called by PositionManager on every scalp position close."""
        self.scalp_capital.record_close(addr, pnl_usd)
        if reason == "stop_loss":
            expiry = time.monotonic() + self.cfg.scalp_stop_cooldown_minutes * 60
            self._stop_cooldowns[addr] = expiry
            logger.info(
                f"[ScalpQueue] Stop cooldown: {addr[:8]} "
                f"({self.cfg.scalp_stop_cooldown_minutes:.0f}min)"
            )

    # ── Feeder A: DexScreener scan ──────────────────────────────

    async def _scan_cycle(self):
        if not self.scalp_capital.has_capacity():
            return
        if len(self._watch) >= self.cfg.scalp_max_watch_candidates:
            # Still check tick gate for existing watches
            for addr in list(self._watch.keys()):
                await self._check_tick_gate(addr)
            return

        pairs = await self._fetch_dex_candidates()
        for pair in pairs:
            addr = (pair.get("baseToken") or {}).get("address", "")
            symbol = (pair.get("baseToken") or {}).get("symbol", "?")
            if not addr or addr in self._watch:
                continue
            if not self._passes_quality_gates(pair, addr):
                continue
            price = float((pair.get("priceUsd") or "0") or 0)
            self._watch[addr] = {
                "symbol": symbol,
                "entry_price": price,
                "entry_ts": time.monotonic(),
            }
            logger.debug(f"[ScalpQueue] Watching {symbol} ({addr[:8]})")

        for addr in list(self._watch.keys()):
            await self._check_tick_gate(addr)

    def _passes_quality_gates(self, pair: dict, addr: str) -> bool:
        if addr in self.open_positions_ref:
            return False
        if time.monotonic() < self._stop_cooldowns.get(addr, 0):
            return False
        if not self.scalp_capital.has_capacity():
            return False
        if len(self._watch) >= self.cfg.scalp_max_watch_candidates:
            return False

        mcap = float(pair.get("marketCap") or 0)
        if mcap < self.cfg.scalp_min_mcap:
            return False

        pair_created_ms = pair.get("pairCreatedAt") or 0
        age_days = (time.time() * 1000 - pair_created_ms) / (86_400 * 1000)
        if age_days < self.cfg.scalp_min_age_days:
            return False

        volume_h24 = float((pair.get("volume") or {}).get("h24") or 0)
        if volume_h24 < self.cfg.scalp_min_volume_h24:
            return False

        price_change_h24 = float((pair.get("priceChange") or {}).get("h24") or 0)
        if price_change_h24 <= 0:
            return False

        return True

    # ── Tick gate ───────────────────────────────────────────────

    async def _check_tick_gate(self, addr: str):
        if addr not in self._watch:
            return

        meta = self._watch[addr]
        symbol = meta["symbol"]

        if addr in self.open_positions_ref:
            del self._watch[addr]
            return

        if not self.scalp_capital.has_capacity():
            return

        apf = self.axiom_price_feed
        if apf is None:
            return

        # Gate 4: price must not have moved > 3% from watch entry
        current_price = (getattr(apf, "_price_cache", {}) or {}).get(addr, 0)
        if current_price <= 0:
            return
        entry_price = meta["entry_price"]
        if entry_price > 0:
            move_pct = abs(current_price - entry_price) / entry_price * 100
            if move_pct > self.cfg.scalp_max_entry_move_pct:
                logger.debug(
                    f"[ScalpQueue] {symbol}: {move_pct:.1f}% move from watch — dropping"
                )
                del self._watch[addr]
                return

        # Gate 1: 3+ consecutive upticks in last 15s
        tick_count = (
            apf.get_tick_count(addr, 15) if hasattr(apf, "get_tick_count") else 0
        )
        if tick_count < self.cfg.scalp_tick_consecutive_min:
            return

        # Gate 3: positive tick trend over 30s
        trend = (
            apf.get_tick_trend(addr, 30) if hasattr(apf, "get_tick_trend") else 0
        )
        if trend <= 0:
            return

        # Gate 2: buy/sell ratio > 0.65 over last 30s
        ratio = self._get_buy_sell_ratio(apf, addr, 30)
        if ratio < self.cfg.scalp_tick_ratio_min:
            return

        # All gates passed — fire entry
        logger.info(
            f"[ScalpQueue] ENTRY {symbol} ({addr[:8]}) "
            f"ticks={tick_count} trend={trend:.3f} ratio={ratio:.2f}"
        )
        del self._watch[addr]

        await self.trader.buy(
            token_address=addr,
            token_symbol=symbol,
            strategy="scalp",
            override_usd=self.cfg.scalp_position_usd,
            reason=f"scalp: ticks={tick_count} trend={trend:.3f} ratio={ratio:.2f}",
        )
        self.scalp_capital.record_open(addr, self.cfg.scalp_position_usd)
        self._stop_cooldowns.pop(addr, None)

    def _get_buy_sell_ratio(self, apf, addr: str, seconds: int) -> float:
        buf = (getattr(apf, "_tick_buffers", {}) or {}).get(addr)
        if not buf:
            return 0.0
        now = time.monotonic()
        cutoff = now - seconds
        recent = [t for t in buf if t[0] >= cutoff]
        if not recent:
            return 0.0
        buys = sum(1 for t in recent if t[1] > 0)
        return buys / len(recent)

    # ── Watch set maintenance ───────────────────────────────────

    def _prune_watch_set(self):
        now_mono = time.monotonic()
        expiry_secs = self.cfg.scalp_watch_expiry_minutes * 60

        to_drop = [
            addr for addr, meta in self._watch.items()
            if now_mono - meta["entry_ts"] > expiry_secs
        ]
        for addr in to_drop:
            logger.debug(f"[ScalpQueue] Expired: {self._watch[addr]['symbol']}")
            del self._watch[addr]

        self._stop_cooldowns = {
            addr: exp for addr, exp in self._stop_cooldowns.items()
            if exp > now_mono
        }

    # ── DexScreener REST ────────────────────────────────────────

    async def _fetch_dex_candidates(self) -> list:
        pairs = []
        async with aiohttp.ClientSession() as session:
            for term in _SEARCH_TERMS[:5]:
                try:
                    url = (
                        f"https://api.dexscreener.com/latest/dex/search"
                        f"?q={term}&chain={_DEX_CHAIN}"
                    )
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            pairs.extend(data.get("pairs") or [])
                except Exception as e:
                    logger.debug(f"[ScalpQueue] DexScreener error ({term}): {e}")
        return pairs
