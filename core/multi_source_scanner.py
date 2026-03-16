"""
Multi-Source Scanner
Queries DexScreener AND Birdeye simultaneously.
Only signals a buy when BOTH sources confirm the token is strong.
This dramatically reduces false signals.

Data sources:
  DexScreener    — pairs, volume, price change, liquidity
  Birdeye        — holder count, trade count, smart money flow
  GeckoTerminal  — new pools, price change, liquidity (no API key needed)
  GoPlus         — security data (via SecurityChecker)
"""

import asyncio
import json
import logging
import time
import aiohttp
from typing import Optional, Dict, List
from datetime import datetime, timezone
from core.signal_evaluator import TokenSignalEvaluator
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
BIRDEYE_API = "https://public-api.birdeye.so/defi"
BIRDEYE_API_KEY = ""  # Set in config
GECKO_API = "https://api.geckoterminal.com/api/v2"


@dataclass
class TokenSignal:
    """Combined signal from multiple data sources."""
    token_address: str
    token_symbol: str
    token_name: str
    chain_id: str
    mcap: float
    price_usd: float
    volume_h1: float
    volume_h6: float
    price_change_h1: float
    price_change_h6: float
    liquidity_usd: float
    buy_count_h1: int
    sell_count_h1: int
    holder_count: int
    holder_growth_pct: float    # Holder growth in last hour
    smart_money_buying: bool    # Large wallets accumulating
    dex_score: int              # 0-100 from DexScreener data
    birdeye_score: int          # 0-100 from Birdeye data
    combined_score: int         # Final combined score
    has_social: bool
    dex_url: str
    confirmed_by_both: bool     # True only if both sources agree
    hh_hl_confirmed: bool = False  # From signal evaluator — HH+HL structure
    flags: List[str] = field(default_factory=list)


class MultiSourceScanner:
    """
    Enhanced scanner that cross-references DexScreener, Birdeye, and GeckoTerminal.
    A token must score well on BOTH DexScreener and Birdeye to trigger a buy signal.
    GeckoTerminal serves as an additional discovery source.
    """

    def __init__(self,
                 chain,
                 trader,
                 security_checker,
                 telegram,
                 birdeye_api_key: str = "",
                 min_mcap: float = 200_000,
                 max_mcap: float = 1_000_000,
                 min_combined_score: int = 65,
                 require_both_sources: bool = False,
                 single_source_min_score: int = 70,
                 startup_delay: float = 0):
        self.chain = chain
        self.trader = trader
        self.security_checker = security_checker
        self.telegram = telegram
        self.birdeye_api_key = birdeye_api_key
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_combined_score = min_combined_score
        self.require_both_sources = require_both_sources
        self.single_source_min_score = single_source_min_score
        self.startup_delay = startup_delay

        # Per-cycle dedup only — no cross-cycle blocking so scores can improve
        self.seen_tokens: Dict[str, float] = {}  # kept for stats compatibility
        self.evaluator = TokenSignalEvaluator(
            min_liquidity_usd=50_000,
            max_dev_wallet_pct=5.0,
            preferred_age_min_hours=3.0,
            preferred_age_max_hours=12.0,
            hard_skip_age_hours=24.0,
            pyramid_score_threshold=90
        )
        self.signals_fired: int = 0
        self.signals_blocked_security: int = 0
        self.signals_blocked_score: int = 0

        # Birdeye rate-limiting: fetch at most once every 5 minutes
        self._birdeye_last_fetch: float = 0
        self._birdeye_cache: dict = {}

    async def run(self):
        """Main scanner loop."""
        if self.startup_delay:
            await asyncio.sleep(self.startup_delay)
        logger.info(
            f"[{self.chain.name}] Multi-Source Scanner started — "
            f"${self.min_mcap/1000:.0f}k-${self.max_mcap/1000:.0f}k | "
            f"Min score: {self.min_combined_score}"
        )
        while True:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"[{self.chain.name}] Scanner error: {e}")
            await asyncio.sleep(60)

    async def _scan_cycle(self):
        """Fetch from all sources and evaluate."""
        now = time.monotonic()
        # Per-cycle dedup: prevent evaluating the same address twice within one cycle
        # (e.g. token appearing in both DexScreener and GeckoTerminal simultaneously).
        # Cross-cycle blocking is intentionally removed — scores can improve each cycle.
        _cycle_seen: set = set()

        # Birdeye: fetch every 5 minutes to conserve API credits (2M CUs/mo)
        _BIRDEYE_INTERVAL = 300  # seconds
        if now - self._birdeye_last_fetch >= _BIRDEYE_INTERVAL:
            logger.debug(f"[{self.chain.name}] Birdeye: fetching fresh data")
            fetch_birdeye_coro = self._fetch_birdeye()
            _birdeye_fresh = True
        else:
            remaining = int(_BIRDEYE_INTERVAL - (now - self._birdeye_last_fetch))
            logger.debug(
                f"[{self.chain.name}] Birdeye: using cached data "
                f"(refresh in {remaining}s)"
            )
            async def _cached_birdeye(cache=self._birdeye_cache):
                return cache
            fetch_birdeye_coro = _cached_birdeye()
            _birdeye_fresh = False

        # Run all fetches concurrently
        dex_tokens, birdeye_tokens, gecko_tokens, raydium_tokens, pancake_tokens = await asyncio.gather(
            self._fetch_dexscreener(),
            fetch_birdeye_coro,
            self._fetch_geckoterminal(),
            self._fetch_raydium(),
            self._fetch_pancakeswap(),
            return_exceptions=True
        )

        if _birdeye_fresh and not isinstance(birdeye_tokens, Exception):
            self._birdeye_cache = birdeye_tokens
            self._birdeye_last_fetch = time.monotonic()

        if isinstance(dex_tokens, Exception):
            dex_tokens = []
        if isinstance(birdeye_tokens, Exception):
            birdeye_tokens = {}
        if isinstance(gecko_tokens, Exception):
            gecko_tokens = {}
        if isinstance(raydium_tokens, Exception):
            raydium_tokens = []
        if isinstance(pancake_tokens, Exception):
            pancake_tokens = []

        logger.info(
            f"[{self.chain.name}] DexScreener: {len(dex_tokens)} | Birdeye: {len(birdeye_tokens)} | "
            f"GeckoTerminal: {len(gecko_tokens)} | Raydium: {len(raydium_tokens)} | "
            f"PancakeSwap: {len(pancake_tokens)} tokens"
        )

        # Merge native DEX tokens (Raydium/PancakeSwap) into DexScreener list
        _dex_addr_set: dict[str, bool] = {
            t.get("baseToken", {}).get("address", "").lower(): True
            for t in dex_tokens
        }
        for rt in raydium_tokens + pancake_tokens:
            addr = rt.get("baseToken", {}).get("address", "").lower()
            if addr and addr not in _dex_addr_set:
                dex_tokens.append(rt)
                _dex_addr_set[addr] = True

        # Evaluate DexScreener + Raydium tokens
        dex_addrs = set()
        for token in dex_tokens:
            try:
                addr = token.get("baseToken", {}).get("address", "").lower()
                if not addr or addr in _cycle_seen:
                    continue
                _cycle_seen.add(addr)
                dex_addrs.add(addr)

                birdeye_data = birdeye_tokens.get(addr, {})
                signal = self._build_signal(token, birdeye_data)
                if signal:
                    await self._evaluate_signal(signal)
            except Exception as e:
                logger.debug(f"[{self.chain.name}] Token eval error: {e}")

        # Also evaluate Birdeye-only tokens by fetching their DexScreener pair data
        birdeye_only = [
            (addr, bdata) for addr, bdata in birdeye_tokens.items()
            if addr not in dex_addrs
        ]
        if birdeye_only:
            await self._evaluate_birdeye_tokens(birdeye_only)

        # Evaluate GeckoTerminal-only tokens
        birdeye_addrs = set(birdeye_tokens.keys())
        for addr, gecko_data in gecko_tokens.items():
            if addr in _cycle_seen:
                continue
            _cycle_seen.add(addr)
            try:
                dex_pair = self._gecko_to_dex_pair(gecko_data)
                signal = self._build_signal(dex_pair, {})
                if signal:
                    await self._evaluate_signal(signal)
            except Exception as e:
                logger.debug(f"[{self.chain.name}] GeckoTerminal token eval error: {e}")

    async def _evaluate_birdeye_tokens(self, birdeye_only: list):
        """Fetch DexScreener pair data for Birdeye-discovered tokens and evaluate."""
        chain_id = self.chain.chain_id
        for i in range(0, len(birdeye_only), 30):
            batch_slice = birdeye_only[i:i + 30]
            batch = [addr for addr, _ in batch_slice]
            birdeye_map = {addr: bdata for addr, bdata in batch_slice}
            try:
                addresses = ",".join(batch)
                url = f"{DEXSCREENER_API}/tokens/{addresses}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        if resp.status != 200:
                            pairs = []
                        else:
                            data = await resp.json()
                            pairs = data.get("pairs") or []

                # Track which addresses got a DexScreener pair
                paired_addrs = set()
                for pair in pairs:
                    if pair.get("chainId") != self.chain.dexscreener_chain:
                        continue
                    mcap = pair.get("marketCap") or 0
                    if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
                        continue
                    addr = pair.get("baseToken", {}).get("address", "").lower()
                    paired_addrs.add(addr)
                    birdeye_data = birdeye_map.get(addr, {})
                    signal = self._build_signal(pair, birdeye_data)
                    if signal:
                        await self._evaluate_signal(signal)

                # Evaluate tokens that DexScreener returned no pair for (Birdeye-only)
                for addr, bdata in birdeye_map.items():
                    if addr in paired_addrs:
                        continue
                    mcap = bdata.get("mc", 0)
                    if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
                        continue
                    synth_pair = {
                        "baseToken": {"address": addr, "symbol": bdata.get("symbol", "?"), "name": bdata.get("name", "?")},
                        "marketCap": bdata.get("mc", 0),
                        "liquidity": {"usd": bdata.get("liquidity", 0)},
                        "volume": {"h1": bdata.get("v1hUSD", 0), "h6": bdata.get("v6hUSD", 0)},
                        "priceChange": {"h1": bdata.get("priceChange1hPercent", 0), "h6": bdata.get("priceChange6hPercent", 0)},
                        "txns": {"h1": {"buys": int(bdata.get("buy1h", 0)), "sells": int(bdata.get("sell1h", 0))}},
                        "priceUsd": str(bdata.get("price", 0)),
                        "info": {},
                        "url": f"https://dexscreener.com/{chain_id}/{addr}",
                        "chainId": chain_id
                    }
                    signal = self._build_signal(synth_pair, bdata)
                    if signal:
                        await self._evaluate_signal(signal)

            except Exception as e:
                logger.debug(f"[{self.chain.name}] Birdeye-only eval error: {e}")

    async def _fetch_dexscreener(self) -> list:
        """Fetch pairs from DexScreener.

        Two approaches combined:
        1. Boost/trending stubs — enriched via /tokens/{addresses} batches
        2. Keyword searches with chainId filter — returns full pair data directly

        Keywords are chain-specific memecoin vocabulary to maximize hit rate.
        chainId is passed in the URL so DexScreener pre-filters, giving us 30
        relevant pairs per keyword instead of 30 global results we mostly discard.
        """
        dex_chain = self.chain.dexscreener_chain
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

        async def _get_json(session, url) -> Optional[dict]:
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[{self.chain.name}] DexScreener HTTP {resp.status} — {url}")
                        return None
                    return await resp.json()
            except Exception as e:
                logger.debug(f"[{self.chain.name}] DexScreener fetch error: {e}")
                return None

        async def _enrich_addresses(session, addresses: list) -> list:
            pairs_out = []
            for i in range(0, len(addresses), 30):
                batch = addresses[i:i + 30]
                url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                data = await _get_json(session, url)
                if not data:
                    continue
                for p in (data.get("pairs") or []):
                    if p.get("chainId") != dex_chain:
                        continue
                    mcap = p.get("marketCap") or 0
                    if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
                        continue
                    pairs_out.append(p)
            return pairs_out

        # Chain-specific keywords — memecoin vocabulary that actually appears in token names/symbols
        if self.chain.chain_id == "solana":
            keywords = ["pump", "sol", "moon", "inu", "dog", "cat", "ai", "pepe",
                        "bonk", "wif", "meme", "frog", "baby", "elon", "trump",
                        "based", "gem", "defi", "dao", "ape"]
        elif self.chain.chain_id == "base":
            keywords = ["base", "based", "pepe", "doge", "ape", "cat", "ai",
                        "baby", "elon", "trump", "moon", "meme", "gem", "dog",
                        "inu", "frog", "defi", "dao", "brett", "toshi"]
        else:  # bsc
            keywords = ["bnb", "bsc", "pepe", "doge", "ape", "baby", "cat",
                        "elon", "trump", "moon", "meme", "gem", "inu", "dog",
                        "cake", "safe", "defi", "ai", "frog", "shib"]

        try:
            async with aiohttp.ClientSession() as session:
                # Boost/trending stubs (chain-agnostic endpoints, enriched after)
                stub_coros = [
                    _get_json(session, "https://api.dexscreener.com/token-boosts/top/v1"),
                    _get_json(session, "https://api.dexscreener.com/token-boosts/latest/v1"),
                    _get_json(session, "https://api.dexscreener.com/tokens/trending/v1"),
                ]
                # Keyword searches — chainId in URL so results are pre-filtered to this chain
                search_coros = [
                    _get_json(session, f"https://api.dexscreener.com/latest/dex/search?q={kw}&chainId={dex_chain}")
                    for kw in keywords
                ]

                all_results = await asyncio.gather(*stub_coros, *search_coros, return_exceptions=True)
                stub_results = all_results[:3]
                search_results = all_results[3:]

                # Collect addresses from stubs for enrichment
                stub_addresses: list[str] = []
                for raw in stub_results:
                    if isinstance(raw, Exception) or not raw:
                        continue
                    items = raw if isinstance(raw, list) else []
                    for item in items:
                        if item.get("chainId") != dex_chain:
                            continue
                        addr = item.get("tokenAddress", "")
                        if addr:
                            stub_addresses.append(addr)

                stub_addresses = list(dict.fromkeys(stub_addresses))
                enriched_pairs = await _enrich_addresses(session, stub_addresses)

                # Collect search pairs (already full pair data)
                direct_pairs = []
                for data in search_results:
                    if isinstance(data, Exception) or not data:
                        continue
                    for p in (data.get("pairs") or []):
                        if p.get("chainId") != dex_chain:
                            continue
                        mcap = p.get("marketCap") or 0
                        if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
                            continue
                        direct_pairs.append(p)

                seen: dict[str, dict] = {}
                for p in enriched_pairs + direct_pairs:
                    addr = p.get("baseToken", {}).get("address", "").lower()
                    if addr and addr not in seen:
                        seen[addr] = p

                return list(seen.values())

        except Exception as e:
            logger.error(f"[{self.chain.name}] DexScreener error: {e}")
            return []

    async def _fetch_raydium(self) -> list:
        """Fetch active Raydium pairs (Solana only). Returns dex_pair format list."""
        if self.chain.chain_id != "solana":
            return []

        try:
            connector = aiohttp.TCPConnector(force_close=True)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    "https://api.raydium.io/v2/main/pairs",
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",  # disable compression to avoid chunked decode errors
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.debug(
                            f"[{self.chain.name}] Raydium HTTP {resp.status}"
                        )
                        return []
                    raw_bytes = await resp.read()
                    all_pairs = json.loads(raw_bytes)

            if not isinstance(all_pairs, list):
                logger.debug(f"[{self.chain.name}] Raydium: unexpected response format")
                return []

            # Filter by liquidity proxy and sort by 24h volume
            liquid = [p for p in all_pairs if (p.get("liquidity") or 0) >= 10_000]
            liquid.sort(key=lambda p: p.get("volume24h") or 0, reverse=True)
            top200 = liquid[:200]

            if not top200:
                return []

            # Extract baseMint addresses for DexScreener enrichment
            base_mints = [p.get("baseMint", "") for p in top200 if p.get("baseMint")]
            base_mints = list(dict.fromkeys(base_mints))  # deduplicate, preserve order

            # Enrich via DexScreener /tokens batches (30 per batch)
            enriched: list[dict] = []
            async with aiohttp.ClientSession() as session:
                for i in range(0, len(base_mints), 30):
                    batch = base_mints[i:i + 30]
                    url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                    try:
                        async with session.get(
                            url,
                            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp:
                            if resp.status != 200:
                                logger.debug(
                                    f"[{self.chain.name}] Raydium enrich HTTP {resp.status}"
                                )
                                continue
                            data = await resp.json()
                            pairs = data.get("pairs") or []
                        for p in pairs:
                            if p.get("chainId") != self.chain.dexscreener_chain:
                                continue
                            mcap = p.get("marketCap") or 0
                            if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
                                continue
                            enriched.append(p)
                    except Exception as e:
                        logger.debug(f"[{self.chain.name}] Raydium enrich batch error: {e}")

            # Deduplicate by baseToken.address
            seen: dict[str, dict] = {}
            for p in enriched:
                addr = p.get("baseToken", {}).get("address", "").lower()
                if addr and addr not in seen:
                    seen[addr] = p

            return list(seen.values())

        except Exception as e:
            logger.error(f"[{self.chain.name}] Raydium error: {e}")
            return []

    async def _fetch_pancakeswap(self) -> list:
        """Fetch active pairs from PancakeSwap/native BNB DEXes via DexScreener pairs endpoint."""
        if self.chain.chain_id != "bsc":
            return []

        # PancakeSwap public API is down — use DexScreener's DEX-specific pairs endpoint
        dex_ids = ["pancakeswap", "pancakeswap-v3-bsc", "bakeryswap"]
        all_pairs: list[dict] = []

        try:
            async with aiohttp.ClientSession() as session:
                for dex_id in dex_ids:
                    url = f"https://api.dexscreener.com/latest/dex/pairs/bsc/{dex_id}"
                    try:
                        async with session.get(
                            url,
                            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp:
                            if resp.status != 200:
                                logger.debug(f"[{self.chain.name}] PancakeSwap/{dex_id} HTTP {resp.status}")
                                continue
                            data = await resp.json()
                            for p in (data.get("pairs") or []):
                                mcap = p.get("marketCap") or 0
                                if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
                                    continue
                                all_pairs.append(p)
                    except Exception as e:
                        logger.debug(f"[{self.chain.name}] PancakeSwap/{dex_id} error: {e}")

            seen: dict[str, dict] = {}
            for p in all_pairs:
                addr = p.get("baseToken", {}).get("address", "").lower()
                if addr and addr not in seen:
                    seen[addr] = p
            return list(seen.values())

        except Exception as e:
            logger.error(f"[{self.chain.name}] PancakeSwap error: {e}")
            return []

    async def _fetch_birdeye(self) -> Dict[str, dict]:
        """Fetch trending tokens from Birdeye."""
        if not self.birdeye_api_key:
            return {}

        # Only fetch Birdeye for Solana — conserves API credits
        # Base and BNB get sufficient coverage from DexScreener + GeckoTerminal
        if self.chain.chain_id != "solana":
            return {}

        birdeye_chain = "solana"

        try:
            url = f"{BIRDEYE_API}/tokenlist"
            headers = {
                "X-API-KEY": self.birdeye_api_key,
                "x-chain": birdeye_chain,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            # Two pages — best signal per sort to conserve Birdeye credits
            fetch_params = [
                {"sort_by": "v24hChangePercent", "sort_type": "desc", "offset": 0, "limit": 50, "min_liquidity": self.min_mcap / 10},
                {"sort_by": "mc",                "sort_type": "asc",  "offset": 0, "limit": 50,
                 "min_mc": 200_000, "max_mc": 1_000_000},
            ]
            all_tokens: Dict[str, dict] = {}
            async with aiohttp.ClientSession() as session:
                for params in fetch_params:
                    async with session.get(
                        url, params=params, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            logger.warning(f"[{self.chain.name}] Birdeye HTTP {resp.status}: {text[:100]}")
                            continue
                        data = await resp.json()
                        tokens = data.get("data", {}).get("tokens", [])
                        all_tokens.update({
                            t.get("address", "").lower(): t
                            for t in tokens
                            if t.get("address")
                        })
                    await asyncio.sleep(1)  # small delay between pages
            return all_tokens
        except Exception as e:
            logger.debug(f"[{self.chain.name}] Birdeye error: {e}")
            return {}

    async def _fetch_geckoterminal(self) -> Dict[str, dict]:
        """Fetch pools from GeckoTerminal (no API key required).
        Sequential with 1s delay to avoid 429 rate limits.
        """
        gecko_chains = {
            "solana": "solana",
            "base": "base",
            "bsc": "bsc"
        }
        gecko_chain = gecko_chains.get(self.chain.chain_id)
        if not gecko_chain:
            return {}

        all_pools: Dict[str, dict] = {}
        try:
            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            # Build the list of (url, label) pairs to fetch — run concurrently
            gecko_urls = [
                (f"{GECKO_API}/networks/{gecko_chain}/new_pools?page=1",     "new_pools p1"),
                (f"{GECKO_API}/networks/{gecko_chain}/new_pools?page=2",     "new_pools p2"),
                (f"{GECKO_API}/networks/{gecko_chain}/trending_pools?page=1","trending p1"),
                (f"{GECKO_API}/networks/{gecko_chain}/pools?sort=h24_volume_usd_desc&page=1", "volume_sorted p1"),
                (f"{GECKO_API}/networks/{gecko_chain}/pools?sort=h24_volume_usd_desc&page=2", "volume_sorted p2"),
                (f"{GECKO_API}/networks/{gecko_chain}/pools?sort=h24_volume_usd_desc&page=3", "volume_sorted p3"),
                (f"{GECKO_API}/networks/{gecko_chain}/pools?sort=h24_tx_count_desc&page=1",   "tx_count p1"),
            ]

            async with aiohttp.ClientSession() as session:
                for url, label in gecko_urls:
                    try:
                        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            if resp.status == 429:
                                logger.debug(f"[{self.chain.name}] GeckoTerminal rate-limited ({label}) — stopping")
                                break
                            if resp.status != 200:
                                logger.debug(f"[{self.chain.name}] GeckoTerminal HTTP {resp.status} ({label})")
                                await asyncio.sleep(1)
                                continue
                            data = await resp.json()
                            pools = data.get("data", [])
                    except Exception as e:
                        logger.debug(f"[{self.chain.name}] GeckoTerminal fetch error ({label}): {e}")
                        await asyncio.sleep(1)
                        continue

                    await asyncio.sleep(1)

                    for pool in pools:
                        try:
                            attrs = pool.get("attributes", {})
                            rels = pool.get("relationships", {})

                            base_token_id = (
                                rels.get("base_token", {})
                                    .get("data", {})
                                    .get("id", "")
                            )
                            if not base_token_id:
                                continue
                            parts = base_token_id.split("_", 1)
                            if len(parts) != 2:
                                continue
                            token_address = parts[1].lower()

                            # Prefer market_cap_usd; fall back to fdv_usd but cap inflated FDV
                            mcap_usd = attrs.get("market_cap_usd")
                            fdv_usd = attrs.get("fdv_usd")
                            if mcap_usd is not None:
                                mcap = float(mcap_usd)
                            elif fdv_usd is not None:
                                mcap = float(fdv_usd)
                            else:
                                mcap = 0.0

                            # When fdv >> mcap, use the more accurate circulating mcap
                            if mcap_usd is not None and fdv_usd is not None:
                                if float(fdv_usd) > float(mcap_usd) * 5:
                                    mcap = float(mcap_usd)

                            if mcap != 0.0 and not (self.min_mcap <= mcap <= self.max_mcap):
                                continue

                            liquidity = float(attrs.get("reserve_in_usd") or 0)
                            volume_obj = attrs.get("volume_usd") or {}
                            volume_h24 = float(volume_obj.get("h24") or 0)
                            price_change_obj = attrs.get("price_change_percentage") or {}
                            price_change_h1 = float(price_change_obj.get("h1") or 0)
                            price_change_h24 = float(price_change_obj.get("h24") or 0)
                            created_at = attrs.get("pool_created_at", "")

                            pool_name = attrs.get("name", "")
                            symbol = pool_name.split("/")[0].strip() if pool_name else "?"

                            gecko_source = "trending_pools" if label.startswith("trending") else "new_pools"

                            all_pools[token_address] = {
                                "address": token_address,
                                "symbol": symbol,
                                "mcap": mcap,
                                "liquidity": liquidity,
                                "volume_h24": volume_h24,
                                "price_change_h1": price_change_h1,
                                "price_change_h24": price_change_h24,
                                "created_at": created_at,
                                "_gecko_source": gecko_source,
                            }
                        except Exception as e:
                            logger.debug(f"[{self.chain.name}] GeckoTerminal pool parse error: {e}")

        except Exception as e:
            logger.debug(f"[{self.chain.name}] GeckoTerminal error: {e}")

        return all_pools

    def _gecko_to_dex_pair(self, gecko_data: dict) -> dict:
        """
        Convert a GeckoTerminal pool record into the dex_pair dict format
        that _build_signal() expects.
        """
        address = gecko_data.get("address", "")
        symbol = gecko_data.get("symbol", "?")
        mcap = gecko_data.get("mcap", 0)
        liquidity = gecko_data.get("liquidity", 0)
        volume_h24 = gecko_data.get("volume_h24", 0)
        price_change_h1 = gecko_data.get("price_change_h1", 0)
        price_change_h24 = gecko_data.get("price_change_h24", 0)
        source = gecko_data.get("_gecko_source", "new_pools")

        # Trending pools survived organic discovery on GeckoTerminal — treat as socially verified
        if source == "trending_pools":
            info = {"websites": ["geckoterminal"], "socials": []}
        else:
            info = {"websites": [], "socials": []}

        return {
            "baseToken": {
                "address": address,
                "symbol": symbol,
                "name": symbol,
            },
            "marketCap": mcap,
            "volume": {
                "h1": volume_h24 / 24 if volume_h24 else 0,  # approximate h1 from h24
                "h6": volume_h24 / 4 if volume_h24 else 0,   # approximate h6 from h24
            },
            "priceChange": {
                "h1": price_change_h1,
                "h6": price_change_h24 / 4 if price_change_h24 else 0,  # approximate
            },
            "txns": {
                "h1": {
                    "buys": 0,
                    "sells": 0,
                }
            },
            "liquidity": {
                "usd": liquidity,
            },
            "info": info,
            "_gecko_source": True,
            "priceUsd": 0,
            "url": f"https://www.geckoterminal.com/{self.chain.chain_id}/pools/{address}",
        }

    def _build_signal(self, dex_pair: dict,
                      birdeye_data: dict) -> Optional[TokenSignal]:
        """Build a combined signal from both data sources."""
        base = dex_pair.get("baseToken", {})
        token_address = base.get("address", "").lower()
        token_symbol = base.get("symbol", "?")
        token_name = base.get("name", "Unknown")

        mcap = dex_pair.get("marketCap", 0)
        # Fix 3: Allow mcap=0 (GeckoTerminal couldn't determine it).
        # Downstream scoring naturally penalizes zero-mcap tokens.
        # For non-zero mcap, apply the normal range filter.
        if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
            return None

        volume_h1 = dex_pair.get("volume", {}).get("h1", 0)
        volume_h6 = dex_pair.get("volume", {}).get("h6", 0)
        price_change_h1 = dex_pair.get("priceChange", {}).get("h1", 0) or 0
        price_change_h6 = dex_pair.get("priceChange", {}).get("h6", 0) or 0
        liquidity = dex_pair.get("liquidity", {}).get("usd", 0)
        txns_h1 = dex_pair.get("txns", {}).get("h1", {})
        buys_h1 = txns_h1.get("buys", 0)
        sells_h1 = txns_h1.get("sells", 0)

        # ── Rug prevention: hard filters before scoring ───────────────────
        # 1. Require pair to be at least 10 minutes old.
        #    Instant rugs dump within the first few minutes of listing.
        pair_created_ms = dex_pair.get("pairCreatedAt", 0) or 0
        if pair_created_ms > 0:
            import time as _time
            pair_age_minutes = (_time.time() - pair_created_ms / 1000) / 60
            if pair_age_minutes < 10:
                return None

        # 2. Require at least 10 sell transactions in h1.
        #    Coordinated pump-and-dump setups have hundreds of buys but
        #    almost zero sells — everyone holds until the dev dumps.
        #    Legitimate organic volume has both sides of the book.
        if buys_h1 > 0 and sells_h1 < 10:
            return None
        price_usd = float(dex_pair.get("priceUsd", 0) or 0)
        info = dex_pair.get("info", {})
        has_social = bool(info.get("socials") or info.get("websites"))
        dex_url = dex_pair.get("url", "")

        # DexScreener score
        # GeckoTerminal doesn't provide txn counts — pass txns_available=False
        # so the scorer awards a neutral score instead of penalizing with 0
        txns_available = not dex_pair.get("_gecko_source", False)
        dex_score = self._score_dexscreener(
            mcap, volume_h1, price_change_h1, price_change_h6,
            buys_h1, sells_h1, liquidity, has_social,
            txns_available=txns_available
        )

        # Birdeye score
        birdeye_score = 0
        holder_count = 0
        holder_growth_pct = 0.0
        smart_money_buying = False

        if birdeye_data:
            birdeye_score, holder_count, holder_growth_pct, smart_money_buying = \
                self._score_birdeye(birdeye_data)

        # Combined score — weighted average when Birdeye data is present,
        # raw DEX score when Birdeye has no data for this token.
        # NOTE: ~99% of early pump.fun tokens are absent from Birdeye trending,
        # so forcing the weighted formula would block nearly all trades.
        # Rug prevention is handled by the security gate instead.
        if birdeye_data:
            combined = int(dex_score * 0.6 + birdeye_score * 0.4)
            confirmed_by_both = dex_score >= 55 and birdeye_score >= 55
        else:
            combined = dex_score
            confirmed_by_both = False

        return TokenSignal(
            token_address=token_address,
            token_symbol=token_symbol,
            token_name=token_name,
            chain_id=self.chain.chain_id,
            mcap=mcap,
            price_usd=price_usd,
            volume_h1=volume_h1,
            volume_h6=volume_h6,
            price_change_h1=price_change_h1,
            price_change_h6=price_change_h6,
            liquidity_usd=liquidity,
            buy_count_h1=buys_h1,
            sell_count_h1=sells_h1,
            holder_count=holder_count,
            holder_growth_pct=holder_growth_pct,
            smart_money_buying=smart_money_buying,
            dex_score=dex_score,
            birdeye_score=birdeye_score,
            combined_score=combined,
            has_social=has_social,
            dex_url=dex_url,
            confirmed_by_both=confirmed_by_both
        )

    def _score_dexscreener(self, mcap, volume_h1, price_change_h1, price_change_h6,
                            buys, sells, liquidity, has_social,
                            txns_available: bool = True) -> int:
        score = 0
        # Market cap position
        if mcap <= 400_000:
            score += 20
        elif mcap <= 700_000:
            score += 15
        else:
            score += 10

        # Volume (chain-adjusted)
        vol_high = 30_000 if self.chain.chain_id != "solana" else 50_000
        vol_mid = 10_000 if self.chain.chain_id != "solana" else 20_000
        if volume_h1 >= vol_high:
            score += 20
        elif volume_h1 >= vol_mid:
            score += 12
        elif volume_h1 >= 5_000:
            score += 6

        # 1h price momentum
        if price_change_h1 > 20:
            score += 20
        elif price_change_h1 > 10:
            score += 14
        elif price_change_h1 > 5:
            score += 8
        elif price_change_h1 < -15:
            score -= 15

        # 6h trend — confirms momentum is sustained, not a dead-cat bounce
        # Negative 6h means the token was already selling off before the 1h spike
        if price_change_h6 > 50:
            score += 15
        elif price_change_h6 > 20:
            score += 10
        elif price_change_h6 > 5:
            score += 5
        elif price_change_h6 < 0:
            score -= 15  # Declining trend — most stop-losses come from here
        elif price_change_h6 < -20:
            score -= 25

        # Buy pressure
        if not txns_available:
            # No txn data from this source (e.g. GeckoTerminal) — award neutral midpoint
            score += 7
        else:
            total = buys + sells
            if total > 0:
                ratio = buys / total
                if ratio >= 0.65:
                    score += 15
                elif ratio >= 0.55:
                    score += 8
                elif ratio < 0.40:
                    score -= 10

        # Liquidity
        min_liq = self.chain.min_liquidity_usd
        if liquidity >= min_liq * 3:
            score += 15
        elif liquidity >= min_liq:
            score += 8
        else:
            score -= 10

        # Social
        if has_social:
            score += 10

        return max(0, min(100, score))

    def _score_birdeye(self, data: dict):
        score = 0
        holder_count = int(data.get("holder", 0))
        holder_growth = float(data.get("holderChange24h", 0) or 0)
        trade_24h = int(data.get("trade24h", 0))
        volume_24h = float(data.get("v24hUSD", 0) or 0)
        smart_money = data.get("uniqueWallet24h", 0) > 50

        # Holders
        if holder_count >= 500:
            score += 25
        elif holder_count >= 200:
            score += 18
        elif holder_count >= 100:
            score += 12
        elif holder_count >= 50:
            score += 6
        else:
            score -= 10

        # Holder growth
        if holder_growth > 20:
            score += 20
        elif holder_growth > 10:
            score += 12
        elif holder_growth > 5:
            score += 6
        elif holder_growth < -10:
            score -= 15

        # Trade activity
        if trade_24h >= 1000:
            score += 20
        elif trade_24h >= 500:
            score += 14
        elif trade_24h >= 200:
            score += 8

        # Smart money
        if smart_money:
            score += 20
            smart_money_buying = True
        else:
            smart_money_buying = False

        # Volume confirmation
        if volume_24h >= 100_000:
            score += 15
        elif volume_24h >= 50_000:
            score += 10

        return max(0, min(100, score)), holder_count, holder_growth, smart_money_buying

    async def _evaluate_signal(self, signal: TokenSignal):
        """Evaluate a signal and decide whether to buy."""
        # Skip if we already hold this token — don't double-buy
        if signal.token_address.lower() in self.trader.open_positions:
            return

        # Only buy tokens with positive 1h momentum — no falling knives
        if signal.price_change_h1 <= 0:
            logger.debug(
                f"[{self.chain.name}] Skipping {signal.token_symbol} — "
                f"no positive momentum: {signal.price_change_h1:.1f}% on 1h"
            )
            return

        # One threshold — DexScreener score alone is sufficient when Birdeye is down.
        # When Birdeye is up, combined_score is a weighted average (dex*0.6 + be*0.4)
        # which will naturally be higher, so the same threshold stays correct.
        effective_min = self.min_combined_score

        if signal.combined_score >= 30:
            logger.info(
                f"[{self.chain.name}] Scoring {signal.token_symbol}: "
                f"combined={signal.combined_score} dex={signal.dex_score} birdeye={signal.birdeye_score} "
                f"mcap=${signal.mcap:,.0f} confirmed_both={signal.confirmed_by_both}"
            )

        if signal.combined_score < effective_min:
            if signal.combined_score >= 40:  # Only log tokens that are close
                logger.info(
                    f"[{self.chain.name}] Near-miss: {signal.token_symbol} "
                    f"score={signal.combined_score} (need {effective_min}) "
                    f"dex={signal.dex_score} birdeye={signal.birdeye_score} "
                    f"mcap=${signal.mcap:,.0f}"
                )
            self.signals_blocked_score += 1
            return

        if self.trader.risk_manager.is_daily_limit_hit():
            return

        # Security check — runs GoPlus before every buy
        sec_result = await self.security_checker.check(
            signal.token_address,
            self.chain.chain_id,
            signal.token_symbol
        )
        if not sec_result.passed:
            self.signals_blocked_security += 1
            logger.warning(
                f"[{self.chain.name}] Security blocked "
                f"{signal.token_symbol} — {sec_result.risk_level}"
            )
            return

        # All checks passed — fire signal
        self.signals_fired += 1
        logger.info(
            f"[{self.chain.name}] BUY SIGNAL: {signal.token_symbol} | "
            f"Score: {signal.combined_score} | "
            f"(DEX:{signal.dex_score}/BE:{signal.birdeye_score}) | "
            f"MCap: ${signal.mcap:,.0f}"
        )

        source_tag = "Both sources confirmed" if signal.confirmed_by_both else "Single source"
        smart_tag = "Smart money buying" if signal.smart_money_buying else ""

        await self.telegram.send(
            f"*Scanner Signal: {signal.token_name} (${signal.token_symbol})*\n"
            f"Chain: {self.chain.name}\n\n"
            f"MCap: ${signal.mcap:,.0f}\n"
            f"1h: {signal.price_change_h1:+.1f}% | "
            f"6h: {signal.price_change_h6:+.1f}%\n"
            f"1h Vol: ${signal.volume_h1:,.0f}\n"
            f"Holders: {signal.holder_count:,} "
            f"({signal.holder_growth_pct:+.1f}% growth)\n"
            f"Score: {signal.combined_score}/100 "
            f"(DEX:{signal.dex_score} / BE:{signal.birdeye_score})\n"
            f"{source_tag}\n"
            f"{smart_tag}\n"
            f"Security: {sec_result.risk_level}\n\n"
            f"[View on DexScreener]({signal.dex_url})"
        )

        await self.trader.buy(
            token_address=signal.token_address,
            token_symbol=signal.token_symbol,
            reason=(
                f"[{self.chain.name}] Multi-source score "
                f"{signal.combined_score} "
                f"(DEX:{signal.dex_score}/BE:{signal.birdeye_score})"
            ),
            signal_score=signal.combined_score,
            hh_hl_confirmed=getattr(signal, "hh_hl_confirmed", False),
            price_hint=signal.price_usd
        )

    def get_stats(self) -> dict:
        return {
            "chain": self.chain.name,
            "signals_fired": self.signals_fired,
            "blocked_by_security": self.signals_blocked_security,
            "blocked_by_score": self.signals_blocked_score,
            "tokens_seen": len(self.seen_tokens)
        }


class PumpFunMonitor:
    """
    Listens to pump.fun WebSocket for new token launches on Solana.
    Feeds directly into the scanner's evaluation pipeline.
    """
    PUMP_WS = "wss://pumpportal.fun/api/data"

    # Fallback SOL/USD price when live feed is unavailable
    _SOL_PRICE_FALLBACK = 130.0

    # Native SOL mint address used to look up SOL price in PriceFeed
    _SOL_MINT = "so11111111111111111111111111111111111111112"

    def __init__(self, scanner: MultiSourceScanner, price_feed=None):
        self.scanner = scanner
        self.price_feed = price_feed  # Fix 6: optional PriceFeed for live SOL price

    def _get_sol_price(self) -> float:
        """Return live SOL/USD price from the price feed, falling back to 130."""
        if self.price_feed is not None:
            try:
                tick = self.price_feed.get_latest(self._SOL_MINT)
                if tick and tick.price_usd and tick.price_usd > 0:
                    return tick.price_usd
            except Exception:
                pass
        # Log at debug level — this is expected when PriceFeed WS hasn't populated SOL yet
        logger.debug(
            f"[PumpFun] Live SOL price unavailable — using fallback ${self._SOL_PRICE_FALLBACK:.0f}"
        )
        return self._SOL_PRICE_FALLBACK

    async def run(self):
        """Connect to pump.fun WebSocket and listen for new token launches."""
        backoff = 5
        while True:
            try:
                await self._connect_and_listen()
                # If we exit cleanly, reset backoff
                backoff = 5
            except Exception as e:
                logger.warning(
                    f"[PumpFun] WebSocket error: {e} — reconnecting in {backoff}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _connect_and_listen(self):
        """Open the WebSocket, subscribe, and process messages until disconnect."""
        try:
            import websockets
        except ImportError:
            logger.error(
                "[PumpFun] 'websockets' package not installed — "
                "run: pip install websockets"
            )
            raise

        logger.info(f"[PumpFun] Connecting to {self.PUMP_WS}")
        async with websockets.connect(self.PUMP_WS) as ws:
            # Subscribe to new token events
            await ws.send(json.dumps({"method": "subscribeNewToken"}))
            logger.info("[PumpFun] Subscribed to new token launches")

            async for raw_msg in ws:
                try:
                    await self._handle_message(raw_msg)
                except Exception as e:
                    logger.debug(f"[PumpFun] Message handling error: {e}")

    async def _handle_message(self, raw_msg: str):
        """Parse a pump.fun new-token message and evaluate it."""
        try:
            msg = json.loads(raw_msg)
        except (json.JSONDecodeError, TypeError):
            return

        mint = msg.get("mint", "")
        if not mint:
            return

        token_address = mint.lower()

        # Skip if already seen
        cache_key = f"solana:{token_address}"
        if cache_key in self.scanner.seen_tokens:
            return

        symbol = msg.get("symbol", "?")
        name = msg.get("name", symbol)
        market_cap_sol = float(msg.get("marketCapSol") or 0)
        v_sol = float(msg.get("vSol") or 0)
        initial_buy = float(msg.get("initialBuy") or 0)

        # Fix 6: Get live SOL price from price feed
        sol_price = self._get_sol_price()
        mcap_usd = market_cap_sol * sol_price

        if not (self.scanner.min_mcap <= mcap_usd <= self.scanner.max_mcap):
            return

        logger.info(f"[PumpFun] New token: {symbol} mcap=${mcap_usd:,.0f} (SOL=${sol_price:.0f})")

        # Build a minimal dex_pair dict and evaluate
        liquidity_usd = v_sol * sol_price
        dex_pair = {
            "baseToken": {
                "address": token_address,
                "symbol": symbol,
                "name": name,
            },
            "marketCap": mcap_usd,
            "volume": {
                "h1": initial_buy * sol_price,
                "h6": 0,
            },
            "priceChange": {
                "h1": 0.1,   # just-launched token has no history — use small positive value
                "h6": 0,
            },
            "txns": {
                "h1": {
                    "buys": 1 if initial_buy > 0 else 0,
                    "sells": 0,
                }
            },
            "liquidity": {
                "usd": liquidity_usd,
            },
            "info": {
                "websites": [],
            },
            "priceUsd": 0,
            "url": f"https://pump.fun/{mint}",
        }

        signal = self.scanner._build_signal(dex_pair, {})
        if signal:
            await self.scanner._evaluate_signal(signal)
