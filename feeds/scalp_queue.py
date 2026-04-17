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
from typing import Dict

logger = logging.getLogger(__name__)

_SCAN_INTERVAL = 45  # seconds
_DEX_CHAIN = "solana"
# Broad memecoin keyword set — runs against DexScreener search. These are the
# most-used memecoin archetypes; each returns a different slice of pairs.
_SEARCH_TERMS = [
    "sol", "bonk", "wif", "cat", "dog", "meme", "pepe", "pump", "baby",
    "inu", "trump", "biden", "moon", "chad", "frog", "fart", "based",
    "ai", "gpt", "turbo", "giga", "elon", "doge", "shib", "trx",
    "404", "69", "420",
]


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

        # Rolling tick-gate rejection counters (reset each scan summary)
        self._tg_no_price = 0
        self._tg_move = 0
        self._tg_ticks = 0
        self._tg_trend = 0
        self._tg_ratio = 0

        # Quality-gate rejection counters (reset each scan summary)
        self._qg_mcap = 0
        self._qg_age = 0
        self._qg_volume = 0
        self._qg_open = 0
        self._qg_cooldown = 0
        self._qg_full = 0

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
            logger.info(
                f"[ScalpQueue] Scan skipped — no capacity "
                f"(open={len(self.scalp_capital._open)}/{self.scalp_capital.max_concurrent})"
            )
            return
        if len(self._watch) >= self.cfg.scalp_max_watch_candidates:
            logger.info(
                f"[ScalpQueue] Watch set full ({len(self._watch)}) — checking tick gate only"
            )
            for addr in list(self._watch.keys()):
                await self._check_tick_gate(addr)
            return

        pairs = await self._fetch_dex_candidates()
        added = 0
        non_sol = 0
        for pair in pairs:
            addr = (pair.get("baseToken") or {}).get("address", "")
            symbol = (pair.get("baseToken") or {}).get("symbol", "?")
            if not addr or addr in self._watch:
                continue
            if (pair.get("chainId") or "").lower() != _DEX_CHAIN or addr.startswith("0x"):
                non_sol += 1
                continue
            if not self._passes_quality_gates(pair, addr):
                continue
            price = float((pair.get("priceUsd") or "0") or 0)
            self._watch[addr] = {
                "symbol": symbol,
                "entry_price": price,
                "entry_ts": time.monotonic(),
            }
            added += 1
            # Subscribe to Axiom real-time price feed so the tick gate has data
            apf = self.axiom_price_feed
            if apf is not None and hasattr(apf, "subscribe_token"):
                try:
                    apf.subscribe_token(addr)
                except Exception as e:
                    logger.debug(f"[ScalpQueue] subscribe failed for {symbol}: {e}")

        for addr in list(self._watch.keys()):
            await self._check_tick_gate(addr)

        logger.info(
            f"[ScalpQueue] Scan: {len(pairs)} pairs (non-sol filtered={non_sol}) → "
            f"+{added} watched (total watch={len(self._watch)}/"
            f"{self.cfg.scalp_max_watch_candidates}) | "
            f"quality-gate rejects: mcap={self._qg_mcap} age={self._qg_age} "
            f"volume={self._qg_volume} open={self._qg_open} "
            f"cooldown={self._qg_cooldown} full={self._qg_full} | "
            f"tick-gate rejects: no_price={self._tg_no_price} move={self._tg_move} "
            f"ticks={self._tg_ticks} trend={self._tg_trend} ratio={self._tg_ratio}"
        )
        self._tg_no_price = 0
        self._tg_move = 0
        self._tg_ticks = 0
        self._tg_trend = 0
        self._tg_ratio = 0
        self._qg_mcap = 0
        self._qg_age = 0
        self._qg_volume = 0
        self._qg_open = 0
        self._qg_cooldown = 0
        self._qg_full = 0

    def _passes_quality_gates(self, pair: dict, addr: str) -> bool:
        # Solana-only — DexScreener /search ignores chain= filter and returns
        # multi-chain pairs. ETH/BSC addresses will never populate an Axiom
        # Solana tick feed, so filter them out at the source.
        if (pair.get("chainId") or "").lower() != _DEX_CHAIN:
            return False
        if addr.startswith("0x"):
            return False

        if addr in self.open_positions_ref:
            self._qg_open += 1
            return False
        if time.monotonic() < self._stop_cooldowns.get(addr, 0):
            self._qg_cooldown += 1
            return False
        if not self.scalp_capital.has_capacity():
            return False
        if len(self._watch) >= self.cfg.scalp_max_watch_candidates:
            self._qg_full += 1
            return False

        mcap = float(pair.get("marketCap") or 0)
        if mcap < self.cfg.scalp_min_mcap:
            self._qg_mcap += 1
            return False

        pair_created_ms = pair.get("pairCreatedAt") or 0
        age_days = (time.time() * 1000 - pair_created_ms) / (86_400 * 1000)
        if age_days < self.cfg.scalp_min_age_days:
            self._qg_age += 1
            return False

        volume_h24 = float((pair.get("volume") or {}).get("h24") or 0)
        if volume_h24 < self.cfg.scalp_min_volume_h24:
            self._qg_volume += 1
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

        # Gate 4: price must not have moved > scalp_max_entry_move_pct from watch entry
        # Axiom stores cache keys as .lower() — try both original and lowered for robustness
        pc = getattr(apf, "price_cache", {}) or {}
        current_price = pc.get(addr) or pc.get(addr.lower(), 0)
        if current_price <= 0:
            self._tg_no_price += 1
            return
        entry_price = meta["entry_price"]
        if entry_price <= 0:
            logger.debug(f"[ScalpQueue] {symbol}: no entry price at watch time — dropping")
            del self._watch[addr]
            return
        move_pct = abs(current_price - entry_price) / entry_price * 100
        if move_pct > self.cfg.scalp_max_entry_move_pct:
            self._tg_move += 1
            del self._watch[addr]
            return

        # Gate 1: N+ consecutive upticks in last 15s
        tick_count = (
            apf.get_tick_count(addr, 15) if hasattr(apf, "get_tick_count") else 0
        )
        if tick_count < self.cfg.scalp_tick_consecutive_min:
            self._tg_ticks += 1
            return

        # Gate 3: positive tick trend over 30s
        trend = (
            apf.get_tick_trend(addr, 30) if hasattr(apf, "get_tick_trend") else 0
        )
        if trend <= 0:
            self._tg_trend += 1
            return

        # Gate 2: buy/sell ratio > scalp_tick_ratio_min over last 30s
        ratio = self._get_buy_sell_ratio(apf, addr, 30)
        if ratio < self.cfg.scalp_tick_ratio_min:
            self._tg_ratio += 1
            return

        # All gates passed — fire entry
        logger.info(
            f"[ScalpQueue] ENTRY {symbol} ({addr[:8]}) "
            f"ticks={tick_count} trend={trend:.3f} ratio={ratio:.2f}"
        )
        del self._watch[addr]

        try:
            await self.trader.buy(
                token_address=addr,
                token_symbol=symbol,
                strategy="scalp",
                override_usd=self.cfg.scalp_position_usd,
                reason=f"scalp: ticks={tick_count} trend={trend:.3f} ratio={ratio:.2f}",
            )
            self.scalp_capital.record_open(addr, self.cfg.scalp_position_usd)
            self._stop_cooldowns.pop(addr, None)
        except Exception as e:
            logger.error(f"[ScalpQueue] Buy failed for {symbol}: {e}")

    def _get_buy_sell_ratio(self, apf, addr: str, seconds: int) -> float:
        tb = getattr(apf, "_tick_buffers", {}) or {}
        buf = tb.get(addr) or tb.get(addr.lower())
        if not buf:
            return 0.0
        now = time.time()
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
        apf = self.axiom_price_feed
        for addr in to_drop:
            logger.debug(f"[ScalpQueue] Expired: {self._watch[addr]['symbol']}")
            del self._watch[addr]
            # Stop streaming prices for expired tokens unless we actually hold them
            if apf is not None and hasattr(apf, "unsubscribe_token") \
                    and addr not in self.open_positions_ref:
                try:
                    apf.unsubscribe_token(addr)
                except Exception:
                    pass

        self._stop_cooldowns = {
            addr: exp for addr, exp in self._stop_cooldowns.items()
            if exp > now_mono
        }

    # ── DexScreener REST ────────────────────────────────────────

    async def _fetch_dex_candidates(self) -> list:
        pairs = []
        seen_addrs: set = set()

        async def _add_pairs(new_pairs):
            for p in new_pairs or []:
                if (p.get("chainId") or "").lower() != _DEX_CHAIN:
                    continue
                base = (p.get("baseToken") or {}).get("address") or ""
                if base and not base.startswith("0x") and base not in seen_addrs:
                    seen_addrs.add(base)
                    pairs.append(p)

        async with aiohttp.ClientSession() as session:
            # 1) Volume/trending-ordered Solana pair search — richest established-token
            #    source (up to ~300 pairs per order, matches scalp profile).
            for order in ("volume", "trending"):
                try:
                    url = (
                        f"https://api.dexscreener.com/latest/dex/search"
                        f"?q={_DEX_CHAIN}&order={order}"
                    )
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            await _add_pairs(data.get("pairs") or [])
                except Exception as e:
                    logger.debug(f"[ScalpQueue] DexScreener order={order} error: {e}")

            # 2) Boosted tokens — curated promoted list. Resolve via tokens/v1.
            boost_addrs: list = []
            for boost_url in (
                "https://api.dexscreener.com/token-boosts/top/v1",
                "https://api.dexscreener.com/token-boosts/latest/v1",
            ):
                try:
                    async with session.get(
                        boost_url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if isinstance(data, list):
                                for item in data:
                                    if (item.get("chainId") or "").lower() == _DEX_CHAIN:
                                        a = item.get("tokenAddress") or ""
                                        if (
                                            a and not a.startswith("0x")
                                            and a not in boost_addrs
                                            and a not in seen_addrs
                                        ):
                                            boost_addrs.append(a)
                except Exception as e:
                    logger.debug(f"[ScalpQueue] DexScreener boosts error: {e}")

            # 3) Passive harvest from Axiom price feed — every token any other scanner
            #    has already subscribed to is a free candidate.
            harvest: list = []
            apf = self.axiom_price_feed
            if apf is not None:
                pool: set = set()
                pc = getattr(apf, "price_cache", {}) or {}
                pool.update(pc.keys())
                subs = getattr(apf, "_subscribed", set()) or set()
                pool.update(subs)
                for a in pool:
                    if (
                        a and not a.startswith("0x")
                        and a not in seen_addrs
                        and a not in boost_addrs
                    ):
                        harvest.append(a)

            # 4) Batch-resolve boost_addrs + harvest via tokens/v1 (30 per call)
            to_resolve = boost_addrs + harvest
            for i in range(0, len(to_resolve), 30):
                batch = to_resolve[i:i + 30]
                try:
                    url = f"https://api.dexscreener.com/tokens/v1/{_DEX_CHAIN}/{','.join(batch)}"
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if isinstance(data, list):
                                await _add_pairs(data)
                except Exception as e:
                    logger.debug(f"[ScalpQueue] DexScreener token batch error: {e}")

            # 5) Keyword search across memecoin terms — fills gaps the orderings miss
            for term in _SEARCH_TERMS:
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
                            await _add_pairs(data.get("pairs") or [])
                except Exception as e:
                    logger.debug(f"[ScalpQueue] DexScreener error ({term}): {e}")
        return pairs
