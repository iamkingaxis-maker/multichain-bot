"""
DipScanner — buys established Solana tokens dipping within an uptrend.

Entry criteria:
  - Market cap >= $1M
  - Pair age >= 7 days
  - 24h volume >= $200k (steady / high activity)
  - 24h price change > 0  (uptrend intact)
  - 1h price change < 0 OR 5m price change < 0  (dip in progress)
  - Not already in open positions
  - Not bought within last 4 hours (per-token cooldown)

Uses DexScreener REST (no API key).
"""

import asyncio
import logging
import time
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

_DEX_CHAIN = "solana"
_SEARCH_TERMS = ["sol", "bonk", "wif", "cat", "dog", "meme", "pepe", "ai", "baby", "pump"]
_SCAN_INTERVAL = 90  # seconds between full scan cycles


class DipScanner:
    def __init__(self,
                 trader,
                 telegram,
                 open_positions_ref: dict,
                 position_usd: float = 500.0,
                 min_mcap: float = 1_000_000,
                 min_age_days: float = 7.0,
                 min_volume_h24: float = 200_000,
                 cooldown_hours: float = 4.0,
                 max_concurrent: int = 3):
        self.trader = trader
        self.telegram = telegram
        self.open_positions_ref = open_positions_ref
        self.position_usd = position_usd
        self.min_mcap = min_mcap
        self.min_age_ms = min_age_days * 86_400 * 1000  # convert to ms
        self.min_volume_h24 = min_volume_h24
        self.cooldown_secs = cooldown_hours * 3600
        self.max_concurrent = max_concurrent

        # per-token cooldown: address -> last buy monotonic time
        self._last_bought: dict[str, float] = {}
        self._start_monotonic = time.monotonic()
        self.signals_fired = 0
        self._last_buy_time = 0.0

    async def run(self):
        logger.info("[DipScanner] Starting — targeting $1M+ mcap dip entries")
        while True:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"[DipScanner] Scan cycle error: {e}")
            await asyncio.sleep(_SCAN_INTERVAL)

    async def _scan_cycle(self):
        # Don't scan if already at max concurrent dip positions
        dip_count = sum(
            1 for pos in self.open_positions_ref.values()
            if getattr(pos, "strategy", "") == "dip_buy"
        )
        if dip_count >= self.max_concurrent:
            logger.debug(f"[DipScanner] At max concurrent ({dip_count}) — skipping scan")
            return

        pairs = await self._fetch_candidates()
        now_ms = time.time() * 1000

        for pair in pairs:
            token_address = (pair.get("baseToken") or {}).get("address", "")
            token_symbol = (pair.get("baseToken") or {}).get("symbol", "?")

            if not token_address:
                continue

            # Skip if already in open positions
            if token_address in self.open_positions_ref:
                continue

            # Skip if bought recently (per-token cooldown)
            last = self._last_bought.get(token_address, 0)
            if last > 0 and (time.monotonic() - last) < self.cooldown_secs:
                continue

            # ── Hard filters ──────────────────────────────────────────
            mcap = pair.get("marketCap") or 0
            if mcap < self.min_mcap:
                continue

            created_ms = pair.get("pairCreatedAt") or 0
            if created_ms <= 0 or (now_ms - created_ms) < self.min_age_ms:
                continue

            vol_h24 = (pair.get("volume") or {}).get("h24", 0) or 0
            if vol_h24 < self.min_volume_h24:
                continue

            # ── Signal: green 24h, red 1h or 5m ─────────────────────
            pc_h24 = (pair.get("priceChange") or {}).get("h24", 0) or 0
            pc_h1 = (pair.get("priceChange") or {}).get("h1", 0) or 0
            pc_m5 = (pair.get("priceChange") or {}).get("m5", 0) or 0

            if pc_h24 <= 0:
                continue  # 24h must be green

            if pc_h1 >= 0 and pc_m5 >= 0:
                continue  # Need at least one red shorter timeframe

            # ── Stop adding once max_concurrent reached mid-cycle ────
            dip_count = sum(
                1 for pos in self.open_positions_ref.values()
                if getattr(pos, "strategy", "") == "dip_buy"
            )
            if dip_count >= self.max_concurrent:
                break

            logger.info(
                f"[DipScanner] Signal: {token_symbol} "
                f"mcap=${mcap/1e6:.1f}M | 24h={pc_h24:+.1f}% 1h={pc_h1:+.1f}% 5m={pc_m5:+.1f}% "
                f"vol24h=${vol_h24/1000:.0f}k"
            )

            self._last_bought[token_address] = time.monotonic()
            self._last_buy_time = time.monotonic()
            self.signals_fired += 1

            await self.trader.buy(
                token_address=token_address,
                token_symbol=token_symbol,
                chain_id="solana",
                override_usd=self.position_usd,
                reason=f"dip_buy: 24h={pc_h24:+.1f}% 1h={pc_h1:+.1f}% 5m={pc_m5:+.1f}%",
                strategy="dip_buy",
            )

    async def _fetch_candidates(self) -> list:
        """Fetch candidate pairs from DexScreener."""
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        pairs_out = []
        seen = set()

        async def _get(session, url) -> Optional[dict]:
            try:
                async with session.get(url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return None
                    return await r.json()
            except Exception:
                return None

        try:
            async with aiohttp.ClientSession() as session:
                urls = [
                    "https://api.dexscreener.com/token-boosts/top/v1",
                    "https://api.dexscreener.com/token-profiles/latest/v1",
                ] + [
                    f"https://api.dexscreener.com/latest/dex/search?q={kw}&chainId={_DEX_CHAIN}"
                    for kw in _SEARCH_TERMS
                ]

                results = await asyncio.gather(*[_get(session, u) for u in urls],
                                               return_exceptions=True)

                # Collect token addresses from stub endpoints for batch enrichment
                stub_addrs = []
                for res in results[:2]:
                    if isinstance(res, (list, dict)):
                        items = res if isinstance(res, list) else res.get("pairs", [])
                        for item in (items or []):
                            addr = item.get("tokenAddress") or item.get("address") or ""
                            if addr:
                                stub_addrs.append(addr)

                # Enrich stub addresses via /tokens batch
                if stub_addrs:
                    for i in range(0, len(stub_addrs), 30):
                        batch = stub_addrs[i:i+30]
                        url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                        data = await _get(session, url)
                        for p in (data or {}).get("pairs", []):
                            if p.get("chainId") == _DEX_CHAIN:
                                addr = (p.get("baseToken") or {}).get("address", "")
                                if addr and addr not in seen:
                                    seen.add(addr)
                                    pairs_out.append(p)

                # Direct pairs from keyword searches
                for res in results[2:]:
                    if isinstance(res, Exception) or not res:
                        continue
                    for p in (res.get("pairs") or []):
                        if p.get("chainId") != _DEX_CHAIN:
                            continue
                        addr = (p.get("baseToken") or {}).get("address", "")
                        if addr and addr not in seen:
                            seen.add(addr)
                            pairs_out.append(p)

        except Exception as e:
            logger.error(f"[DipScanner] Fetch error: {e}")

        return pairs_out
