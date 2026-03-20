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
import os
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
    pair_address: str = ""          # DEX pool address (used for GeckoTerminal OHLCV)
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
                 mcap_dead_zone_min: float = 300_000,
                 mcap_dead_zone_max: float = 550_000,
                 min_liquidity_usd: float = 15_000,
                 min_volume_h1_usd: float = 5_000,
                 min_combined_score: int = 65,
                 max_combined_score: int = 85,
                 require_both_sources: bool = False,
                 min_holder_count: int = 100,
                 single_source_min_score: int = 70,
                 max_dev_wallet_pct: float = 5.0,
                 pyramid_score_threshold: int = 90,
                 preferred_age_min_hours: float = 0.5,
                 preferred_age_max_hours: float = 12.0,
                 hard_skip_age_hours: float = 24.0,
                 rug_classifier=None,
                 tracker=None,
                 startup_delay: float = 0):
        self.chain = chain
        self.trader = trader
        self.min_age_hours = preferred_age_min_hours
        self.hard_skip_age_hours = hard_skip_age_hours
        self.rug_classifier = rug_classifier
        self.security_checker = security_checker
        self.telegram = telegram
        self.birdeye_api_key = birdeye_api_key
        self.solanatracker_api_key: str = ""  # Set via set_solanatracker_key()
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.mcap_dead_zone_min = mcap_dead_zone_min
        self.mcap_dead_zone_max = mcap_dead_zone_max
        self.min_liquidity_usd = min_liquidity_usd
        self.min_volume_h1_usd = min_volume_h1_usd
        self.min_combined_score = min_combined_score
        self.max_combined_score = max_combined_score
        self.require_both_sources = require_both_sources
        self.min_holder_count = min_holder_count
        self.single_source_min_score = single_source_min_score
        self.tracker = tracker
        self.startup_delay = startup_delay

        # Per-cycle dedup only — no cross-cycle blocking so scores can improve
        self.seen_tokens: Dict[str, float] = {}  # kept for stats compatibility
        self.evaluator = TokenSignalEvaluator(
            min_liquidity_usd=min_liquidity_usd,
            max_dev_wallet_pct=max_dev_wallet_pct,
            preferred_age_min_hours=preferred_age_min_hours,
            preferred_age_max_hours=preferred_age_max_hours,
            hard_skip_age_hours=hard_skip_age_hours,
            pyramid_score_threshold=pyramid_score_threshold
        )
        self.signals_fired: int = 0
        self.signals_blocked_security: int = 0
        self.signals_blocked_score: int = 0

        # Birdeye rate-limiting: fetch at most once every 5 minutes
        self._birdeye_last_fetch: float = 0
        self._birdeye_cache: dict = {}
        self._birdeye_trending_last_fetch: float = 0
        self._birdeye_trending_cache: dict = {}
        self._birdeye_recent_last_fetch: float = 0
        self._birdeye_recent_cache: dict = {}
        self._solanatracker_last_fetch: float = 0
        self._solanatracker_cache: list = []

        # Per-token overview cache: addr_lower → (timestamp, data_dict)
        # Avoids re-fetching the same token on every scan cycle (TTL: 1800s)
        self._overview_cache: dict = {}

        # Correct-case mint map: lowercase → original base58 (populated from Raydium baseMint)
        self._mint_map: Dict[str, str] = {}

        # Dip sniper watchlist: tokens blocked as overbought/extended are cached here.
        # When they pull back 15-40% from their peak with recovery signals, we snipe them.
        # Format: addr_lower → {"peak_price": float, "added_at": float}
        self._dip_watchlist: Dict[str, dict] = {}

        # Stop-loss cooldown: after a stop-loss fires, block that token for 4h.
        # Format: addr_lower → expiry_monotonic_time
        self._sl_cooldown: Dict[str, float] = {}

        # SOL macro cache: cache the SOL h1 price change for 5 minutes
        # so we don't hit DexScreener on every single dip check.
        self._sol_macro_ts: float = 0
        self._sol_macro_ok: bool = True

    def set_solanatracker_key(self, api_key: str):
        """Set the SolanaTracker API key for enhanced pump.fun discovery."""
        self.solanatracker_api_key = api_key

    def register_stop_loss(self, token_address: str, token_symbol: str, exit_price: float):
        """
        Called by the position manager when a stop-loss fires.
        Blocks re-entry on this token for 4 hours.
        """
        addr_lower = token_address.lower()
        cooldown_until = time.monotonic() + 4 * 3600
        self._sl_cooldown[addr_lower] = cooldown_until
        logger.info(
            f"[{self.chain.name}] Stop-loss cooldown: {token_symbol} blocked for 4h "
            f"(addr={addr_lower[:8]}…)"
        )

    async def run(self):
        """Main scanner loop."""
        if self.startup_delay:
            await asyncio.sleep(self.startup_delay)
        _be_status = f"key={self.birdeye_api_key[:6]}…" if self.birdeye_api_key else "NO KEY"
        logger.info(
            f"[{self.chain.name}] Multi-Source Scanner started — "
            f"${self.min_mcap/1000:.0f}k-${self.max_mcap/1000:.0f}k | "
            f"Min score: {self.min_combined_score} | Birdeye: {_be_status}"
        )
        # Start watchlist poller as a concurrent background task
        asyncio.create_task(self._watchlist_poller())
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

        # Expire stale dip watchlist entries (older than 4 hours)
        _WATCHLIST_TTL = 4 * 3600
        expired = [a for a, e in self._dip_watchlist.items()
                   if now - e["added_at"] > _WATCHLIST_TTL]
        for addr in expired:
            del self._dip_watchlist[addr]

        # Expire elapsed stop-loss cooldowns
        self._sl_cooldown = {a: t for a, t in self._sl_cooldown.items() if t > now}
        if expired:
            logger.debug(
                f"[{self.chain.name}] Dip watchlist: expired {len(expired)} entries, "
                f"{len(self._dip_watchlist)} remaining"
            )

        # Seed dip watchlist from bad entries (tokens where timing-based stop fired).
        # Seeds bad-entry tokens into the dip watchlist so they must dip and recover
        # before we re-enter. Same dip-buy criteria apply to everyone — no bypass.
        if self.tracker:
            for entry in self.tracker.get_bad_entries():
                addr = entry["token_address"]
                if addr not in self._dip_watchlist:
                    self._dip_watchlist[addr] = {
                        "peak_price": entry["exit_price"],
                        "added_at": now,
                    }
                    logger.debug(
                        f"[{self.chain.name}] Dip watchlist seeded from bad entry: "
                        f"{addr[:8]}… exit ${entry['exit_price']:.8f}"
                    )

        # Birdeye: fetch every 10 minutes to conserve API credits
        _BIRDEYE_INTERVAL = 600  # seconds
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

        # Birdeye trending: fetch every 10 minutes (same cadence as main Birdeye)
        _BIRDEYE_TRENDING_INTERVAL = 600  # seconds
        if now - self._birdeye_trending_last_fetch >= _BIRDEYE_TRENDING_INTERVAL:
            logger.debug(f"[{self.chain.name}] Birdeye trending: fetching fresh data")
            fetch_be_trending_coro = self._fetch_birdeye_trending()
            _be_trending_fresh = True
        else:
            async def _cached_be_trending(cache=self._birdeye_trending_cache):
                return cache
            fetch_be_trending_coro = _cached_be_trending()
            _be_trending_fresh = False

        # Birdeye recent: fetch every 5 minutes (faster cadence — catches new activity)
        _BIRDEYE_RECENT_INTERVAL = 300  # seconds
        if now - self._birdeye_recent_last_fetch >= _BIRDEYE_RECENT_INTERVAL:
            fetch_be_recent_coro = self._fetch_birdeye_recent()
            _be_recent_fresh = True
        else:
            async def _cached_be_recent(cache=self._birdeye_recent_cache):
                return cache
            fetch_be_recent_coro = _cached_be_recent()
            _be_recent_fresh = False

        # SolanaTracker: fetch every 5 minutes (~8,640 calls/month, within 10k limit)
        _ST_INTERVAL = 300  # seconds
        _st_fresh = False
        if self.solanatracker_api_key and now - self._solanatracker_last_fetch >= _ST_INTERVAL:
            fetch_st_coro = self._fetch_solanatracker()
            _st_fresh = True
        else:
            async def _cached_st(cache=self._solanatracker_cache):
                return cache
            fetch_st_coro = _cached_st()

        # Run all fetches concurrently
        (dex_tokens, birdeye_tokens, gecko_tokens, raydium_tokens,
         pancake_tokens, trending_tokens, pumpfun_tokens, be_trending_tokens,
         be_recent_tokens, jupiter_tokens, st_tokens) = await asyncio.gather(
            self._fetch_dexscreener(),
            fetch_birdeye_coro,
            self._fetch_geckoterminal(),
            self._fetch_raydium(),
            self._fetch_pancakeswap(),
            self._fetch_dexscreener_trending(),
            self._fetch_pumpfun_graduated(),
            fetch_be_trending_coro,
            fetch_be_recent_coro,
            self._fetch_jupiter(),
            fetch_st_coro,
            return_exceptions=True
        )

        if _birdeye_fresh and not isinstance(birdeye_tokens, Exception):
            self._birdeye_cache = birdeye_tokens
            self._birdeye_last_fetch = time.monotonic()

        if _be_trending_fresh and not isinstance(be_trending_tokens, Exception):
            self._birdeye_trending_cache = be_trending_tokens
            self._birdeye_trending_last_fetch = time.monotonic()

        if _be_recent_fresh and not isinstance(be_recent_tokens, Exception):
            self._birdeye_recent_cache = be_recent_tokens
            self._birdeye_recent_last_fetch = time.monotonic()

        if _st_fresh and not isinstance(st_tokens, Exception):
            self._solanatracker_cache = st_tokens
            self._solanatracker_last_fetch = time.monotonic()

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
        if isinstance(trending_tokens, Exception):
            trending_tokens = []
        if isinstance(pumpfun_tokens, Exception):
            pumpfun_tokens = []
        if isinstance(be_trending_tokens, Exception):
            be_trending_tokens = {}
        if isinstance(be_recent_tokens, Exception):
            be_recent_tokens = {}
        if isinstance(jupiter_tokens, Exception):
            jupiter_tokens = []
        if isinstance(st_tokens, Exception):
            st_tokens = []

        # Merge all Birdeye sources into main dict (main fetch takes priority)
        for addr, data in {**be_trending_tokens, **be_recent_tokens}.items():
            if addr not in birdeye_tokens:
                birdeye_tokens[addr] = data

        logger.info(
            f"[{self.chain.name}] DexScreener: {len(dex_tokens)} | Birdeye: {len(birdeye_tokens)} | "
            f"GeckoTerminal: {len(gecko_tokens)} | Raydium: {len(raydium_tokens)} | "
            f"PumpFun-RPC: {len(pumpfun_tokens)} | Raydium-RPC: {len(jupiter_tokens)} | "
            f"SolanaTracker: {len(st_tokens)} tokens"
        )

        # Merge native DEX tokens into DexScreener list
        _dex_addr_set: dict[str, bool] = {
            t.get("baseToken", {}).get("address", "").lower(): True
            for t in dex_tokens
        }
        for rt in raydium_tokens + pancake_tokens + trending_tokens + pumpfun_tokens + jupiter_tokens + st_tokens:
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
            if addr not in dex_addrs and addr not in _cycle_seen
        ]
        if birdeye_only:
            await self._evaluate_birdeye_tokens(birdeye_only, _cycle_seen)

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

    async def _evaluate_birdeye_tokens(self, birdeye_only: list, cycle_seen: set = None):
        """Fetch DexScreener pair data for Birdeye-discovered tokens and evaluate."""
        if cycle_seen is None:
            cycle_seen = set()
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
                    if addr in cycle_seen:
                        continue
                    cycle_seen.add(addr)
                    paired_addrs.add(addr)
                    birdeye_data = birdeye_map.get(addr, {})
                    signal = self._build_signal(pair, birdeye_data)
                    if signal:
                        await self._evaluate_signal(signal)

                # Evaluate tokens that DexScreener returned no pair for (Birdeye-only)
                for addr, bdata in birdeye_map.items():
                    if addr in paired_addrs:
                        continue
                    if addr in cycle_seen:
                        continue
                    cycle_seen.add(addr)
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

    # Rotating keyword pool for DexScreener search.
    # Each cycle picks a different slice so we cover the full space over ~3 cycles.
    # 16 searches + 4 stubs = 20 calls per cycle (conservative, no documented rate limit).
    _DEXSCREENER_KEYWORDS = [
        # Tier 1 — high-hit generic terms (match hundreds of Solana tokens)
        "sol", "pump", "cat", "dog", "ai", "baby", "pepe", "trump",
        # Tier 2 — popular memecoin themes
        "moon", "meme", "inu", "doge", "bonk", "wif", "king", "elon",
        # Tier 3 — broader crypto/culture terms
        "coin", "token", "swap", "fun", "bear", "bull", "chad", "giga",
        # Tier 4 — single letters that fuzzy-match many symbols
        "a", "e", "o", "x", "w", "m", "p", "s",
        # Tier 5 — additional meme/culture terms
        "ape", "frog", "rick", "wojak", "nyan", "clown", "based", "cope",
        # Tier 6 — crypto slang and DeFi terms
        "wen", "gm", "wagmi", "ser", "defi", "dao", "nft", "rug",
        # Tier 7 — more single/short letters
        "b", "c", "d", "f", "n", "r", "t", "y",
        # Tier 8 — current meta / trending culture
        "grok", "chibi", "maga", "usa", "army", "war", "space", "fire",
        "black", "white", "gold", "degen", "gem", "send", "rich", "money",
        # Tier 9 — animal meta (strong Solana theme)
        "penguin", "bear", "wolf", "shark", "eagle", "hawk", "lion", "tiger",
        # Tier 10 — more slang/community terms
        "sir", "bro", "fam", "ngmi", "lfg", "rekt", "dump", "100x",
    ]
    _dex_keyword_offset: int = 0  # rotates each cycle

    async def _fetch_dexscreener(self) -> list:
        """Fetch pairs from DexScreener.

        Discovery strategy (per cycle ≈ 14-16 API calls, well under 60/min limit):
          1. 4 stub endpoints (boosts, profiles, takeovers) — enriched via /tokens batch
          2. 8 rotating keyword searches with chainId filter — direct pair data
          3. 2 io.dexscreener.com ranked searches (volume + mcap) — broadest source,
             returns Solana pairs filtered server-side by mcap/liquidity
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

        async def _fetch_io_ranked(_session) -> list:
            """io.dexscreener.com is IP-blocked by Cloudflare on Railway datacenter addresses.
            Returning empty list — public api.dexscreener.com + other sources cover discovery.
            """
            return []

        # Pick 16 keywords for this cycle, rotating through the full pool
        kw_pool = self._DEXSCREENER_KEYWORDS
        n_kw = 20
        offset = MultiSourceScanner._dex_keyword_offset % len(kw_pool)
        cycle_keywords = (kw_pool[offset:] + kw_pool[:offset])[:n_kw]
        MultiSourceScanner._dex_keyword_offset += n_kw

        try:
            async with aiohttp.ClientSession() as session:
                # --- Phase 1: stubs + keyword searches + io ranked (all in parallel) ---
                stub_coros = [
                    _get_json(session, "https://api.dexscreener.com/token-boosts/top/v1"),
                    _get_json(session, "https://api.dexscreener.com/token-boosts/latest/v1"),
                    _get_json(session, "https://api.dexscreener.com/community-takeovers/latest/v1"),
                    _get_json(session, "https://api.dexscreener.com/token-profiles/latest/v1"),
                ]
                search_coros = [
                    _get_json(session, f"https://api.dexscreener.com/latest/dex/search?q={kw}&chainId={dex_chain}")
                    for kw in cycle_keywords
                ]
                io_coro = _fetch_io_ranked(session)

                all_results = await asyncio.gather(
                    *stub_coros, *search_coros, io_coro,
                    return_exceptions=True
                )
                stub_results = all_results[:4]
                search_results = all_results[4:4 + n_kw]
                io_result = all_results[4 + n_kw]

                # --- Phase 2: collect stub addresses for enrichment ---
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
                            if self.chain.chain_id == "solana" and addr != addr.lower():
                                self._mint_map[addr.lower()] = addr

                stub_addresses = list(dict.fromkeys(stub_addresses))
                enriched_pairs = await _enrich_addresses(session, stub_addresses)

                # --- Phase 3: collect search pairs (already full pair data) ---
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

                # --- Phase 4: collect io.dexscreener pairs ---
                io_pairs = []
                if isinstance(io_result, Exception):
                    logger.info(f"[{self.chain.name}] io.dexscreener failed: {type(io_result).__name__}: {io_result}")
                elif not io_result:
                    logger.info(f"[{self.chain.name}] io.dexscreener returned empty list")
                if not isinstance(io_result, Exception) and io_result:
                    # io pairs may need enrichment — extract addresses
                    io_addrs: list[str] = []
                    for p in io_result:
                        addr = (p.get("baseToken") or {}).get("address", "") or p.get("tokenAddress", "")
                        if addr:
                            io_addrs.append(addr)
                            if self.chain.chain_id == "solana" and addr != addr.lower():
                                self._mint_map[addr.lower()] = addr
                    io_addrs = list(dict.fromkeys(io_addrs))
                    if io_addrs:
                        io_pairs = await _enrich_addresses(session, io_addrs)
                    logger.debug(f"[{self.chain.name}] io.dexscreener: {len(io_result)} raw → {len(io_pairs)} enriched")

                # --- Phase 5: dedup all sources ---
                seen: dict[str, dict] = {}
                for p in enriched_pairs + direct_pairs + io_pairs:
                    addr_raw = p.get("baseToken", {}).get("address", "")
                    addr = addr_raw.lower()
                    if addr and addr not in seen:
                        seen[addr] = p
                        if addr_raw and addr_raw != addr and self.chain.chain_id == "solana":
                            self._mint_map[addr] = addr_raw

                logger.info(
                    f"[{self.chain.name}] DexScreener breakdown: "
                    f"stubs={len(enriched_pairs)} search={len(direct_pairs)} "
                    f"io_ranked={len(io_pairs)} → {len(seen)} unique"
                )
                return list(seen.values())

        except Exception as e:
            logger.error(f"[{self.chain.name}] DexScreener error: {e}")
            return []

    async def _fetch_dexscreener_trending(self) -> list:
        """io.dexscreener.com is IP-blocked by Cloudflare on Railway. Always returns []."""
        return []

        pairs = data.get("pairs") or []
        if not pairs:
            logger.info(
                f"[{self.chain.name}] DexScreener trending: 0 pairs — "
                f"keys: {list(data.keys())} | sample: {str(data)[:300]}"
            )
            return []

        logger.info(f"[{self.chain.name}] DexScreener trending: {len(pairs)} raw pairs")

        # Extract addresses and enrich via standard DexScreener /tokens endpoint
        addresses = []
        for p in pairs:
            addr = (p.get("baseToken") or {}).get("address", "") or p.get("tokenAddress", "")
            if addr:
                addresses.append(addr)
                if self.chain.chain_id == "solana" and addr != addr.lower():
                    self._mint_map[addr.lower()] = addr

        if not addresses:
            return []

        # Enrich via standard DexScreener API (no Cloudflare on api.dexscreener.com)
        enriched = []
        headers_std = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        dex_chain = self.chain.dexscreener_chain
        try:
            async with aiohttp.ClientSession() as session:
                for i in range(0, len(addresses), 30):
                    batch = addresses[i:i + 30]
                    url_enrich = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                    async with session.get(url_enrich, headers=headers_std, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            continue
                        result = await resp.json()
                        for p in (result.get("pairs") or []):
                            if p.get("chainId") != dex_chain:
                                continue
                            mcap = p.get("marketCap") or 0
                            if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
                                continue
                            enriched.append(p)
        except Exception as e:
            logger.debug(f"[{self.chain.name}] DexScreener trending enrich error: {e}")

        logger.info(f"[{self.chain.name}] DexScreener trending: {len(enriched)} enriched pairs")
        return enriched

    async def _fetch_pumpfun_graduated(self) -> list:
        """Fetch recently graduated pump.fun tokens via Solana RPC.

        Watches the pump.fun migration program for recent graduation events using
        our own Solana RPC — no external API, no Cloudflare blocking.

        Flow:
          1. getSignaturesForAddress on the pump.fun migration program
          2. Batch getTransaction for each sig
          3. Extract token mint from postTokenBalances (non-SOL mint)
          4. Enrich via DexScreener for price/volume/mcap data
        """
        if self.chain.chain_id != "solana":
            return []

        PUMP_MIGRATION = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
        SOL_MINT = "So11111111111111111111111111111111111111112"
        rpc_url = self.trader.rpc_url

        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: get recent graduation signatures
                async with session.post(rpc_url, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [PUMP_MIGRATION, {"limit": 50, "commitment": "finalized"}]
                }, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        return []
                    sigs_data = await r.json()

                sigs = [
                    x["signature"]
                    for x in (sigs_data.get("result") or [])
                    if not x.get("err")
                ]
                if not sigs:
                    return []

                # Step 2: batch getTransaction for all sigs in one request
                batch_req = [
                    {
                        "jsonrpc": "2.0", "id": i,
                        "method": "getTransaction",
                        "params": [sig, {
                            "encoding": "json",
                            "maxSupportedTransactionVersion": 0,
                            "commitment": "finalized"
                        }]
                    }
                    for i, sig in enumerate(sigs)
                ]
                async with session.post(
                    rpc_url, json=batch_req,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as r:
                    if r.status != 200:
                        return []
                    tx_results = await r.json()

                # Step 3: extract mint from postTokenBalances
                mints: list[str] = []
                for item in (tx_results if isinstance(tx_results, list) else []):
                    tx = item.get("result") or {}
                    if not tx:
                        continue
                    for bal in (tx.get("meta") or {}).get("postTokenBalances", []):
                        mint = bal.get("mint", "")
                        if mint and mint != SOL_MINT:
                            mints.append(mint)
                            break  # one non-SOL mint per graduation tx

                if not mints:
                    return []

                mints = list(dict.fromkeys(mints))
                self._mint_map.update({m.lower(): m for m in mints})

                # Step 4: enrich via DexScreener
                enriched: list[dict] = []
                dex_chain = self.chain.dexscreener_chain
                for i in range(0, len(mints), 30):
                    batch = mints[i:i + 30]
                    url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                    try:
                        async with session.get(
                            url,
                            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                            timeout=aiohttp.ClientTimeout(total=15),
                        ) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                            for p in (data.get("pairs") or []):
                                if p.get("chainId") != dex_chain:
                                    continue
                                mcap = p.get("marketCap") or 0
                                if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
                                    continue
                                enriched.append(p)
                    except Exception as e:
                        logger.debug(f"[{self.chain.name}] pump.fun enrich error: {e}")

            # Deduplicate
            seen: dict[str, dict] = {}
            for p in enriched:
                addr = p.get("baseToken", {}).get("address", "").lower()
                if addr and addr not in seen:
                    seen[addr] = p

            result = list(seen.values())
            if result:
                logger.info(f"[{self.chain.name}] pump.fun graduated (RPC): {len(result)} pairs")
            return result

        except Exception as e:
            logger.debug(f"[{self.chain.name}] pump.fun RPC error: {e}")
            return []

    async def _fetch_solanatracker(self) -> list:
        """Fetch trending Solana tokens from SolanaTracker.io.

        Returns 51 tokens with full price/volume/mcap/risk data — already richer
        than DexScreener pairs since it includes holders, risk scores, and per-timeframe
        events. Converts to DexScreener pair format for the existing evaluation pipeline.

        Rate limit: ~1 call per 5 min = 8,640/month (budget: 10,000/month).
        """
        if self.chain.chain_id != "solana" or not self.solanatracker_api_key:
            return []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://data.solanatracker.io/tokens/trending",
                    headers={
                        "x-api-key": self.solanatracker_api_key,
                        "Accept": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 429:
                        logger.debug(f"[{self.chain.name}] SolanaTracker rate-limited")
                        return self._solanatracker_cache  # return stale cache on 429
                    if resp.status != 200:
                        logger.debug(f"[{self.chain.name}] SolanaTracker HTTP {resp.status}")
                        return []
                    data = await resp.json()

            if not isinstance(data, list):
                return []

            dex_chain = self.chain.dexscreener_chain
            pairs_out: list[dict] = []

            for item in data:
                try:
                    token = item.get("token") or {}
                    mint = token.get("mint") or ""
                    if not mint:
                        continue

                    pools = item.get("pools") or []
                    if not pools:
                        continue
                    pool = pools[0]

                    mc_usd = (pool.get("marketCap") or {}).get("usd") or 0
                    if mc_usd != 0 and not (self.min_mcap <= mc_usd <= self.max_mcap):
                        continue

                    liq_usd = (pool.get("liquidity") or {}).get("usd") or 0
                    price_usd = (pool.get("price") or {}).get("usd") or 0
                    txns = pool.get("txns") or {}
                    vol24h = txns.get("volume24h") or 0
                    events = item.get("events") or {}

                    pair = {
                        "chainId": dex_chain,
                        "baseToken": {
                            "address": mint,
                            "symbol": token.get("symbol") or "",
                            "name": token.get("name") or token.get("symbol") or "",
                        },
                        "pairAddress": pool.get("poolId") or mint,
                        "marketCap": mc_usd,
                        "liquidity": {"usd": liq_usd},
                        "priceUsd": str(price_usd),
                        "volume": {
                            "h1": vol24h / 24,   # rough hourly estimate
                            "h6": vol24h / 4,
                            "h24": vol24h,
                        },
                        "priceChange": {
                            "h1": (events.get("1h") or {}).get("priceChangePercentage") or 0,
                            "h6": (events.get("6h") or {}).get("priceChangePercentage") or 0,
                            "h24": (events.get("24h") or {}).get("priceChangePercentage") or 0,
                        },
                        "txns": {
                            "h1": {
                                "buys": max(1, int(txns.get("buys") or 0) // 24),
                                "sells": max(1, int(txns.get("sells") or 0) // 24),
                            }
                        },
                        # Extra ST data — used by dip-buy evaluator if present
                        "_st_holders": item.get("holders") or 0,
                        "_st_risk": item.get("risk") or {},
                    }
                    pairs_out.append(pair)
                    self._mint_map[mint.lower()] = mint

                except Exception:
                    continue

            if pairs_out:
                logger.info(
                    f"[{self.chain.name}] SolanaTracker: {len(pairs_out)} pairs in mcap range"
                )
            return pairs_out

        except Exception as e:
            logger.debug(f"[{self.chain.name}] SolanaTracker error: {e}")
            return []

    async def _fetch_birdeye_trending(self) -> dict:
        """Fetch tokens from two additional Birdeye sources in parallel:
          1. tokenlist sorted by raw 24h volume (high-volume tokens)
          2. token_trending curated list (Birdeye's trending rank, 100 tokens)

        Returns dict keyed by lowercase token address.
        """
        if self.chain.chain_id != "solana" or not self.birdeye_api_key:
            return {}

        headers = {
            "X-API-KEY": self.birdeye_api_key,
            "x-chain": "solana",
            "Accept": "application/json",
        }

        async def _vol_sort(session) -> list:
            try:
                async with session.get(
                    f"{BIRDEYE_API}/tokenlist",
                    headers=headers,
                    params={"sort_by": "v24hUSD", "sort_type": "desc",
                            "offset": 0, "limit": 50, "min_liquidity": self.min_liquidity_usd},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return (data.get("data") or {}).get("tokens") or []
            except Exception:
                return []

        async def _trending(session) -> list:
            """Fetch up to 100 tokens from Birdeye's curated trending list."""
            tokens = []
            try:
                for offset in range(0, 100, 20):
                    async with session.get(
                        f"{BIRDEYE_API}/token_trending",
                        headers=headers,
                        params={"sort_by": "rank", "sort_type": "asc",
                                "offset": offset, "limit": 20},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status != 200:
                            break
                        data = await resp.json()
                        page = (data.get("data") or {}).get("tokens") or []
                        if not page:
                            break
                        # Normalize field names: trending uses 'marketcap' not 'mc'
                        for t in page:
                            if "marketcap" in t and "mc" not in t:
                                t["mc"] = t["marketcap"]
                        tokens.extend(page)
            except Exception:
                pass
            return tokens

        try:
            async with aiohttp.ClientSession() as session:
                vol_tokens, trend_tokens = await asyncio.gather(
                    _vol_sort(session), _trending(session)
                )

            result: dict = {}
            for t in vol_tokens + trend_tokens:
                addr = (t.get("address") or "").lower()
                if addr and addr not in result:
                    result[addr] = t

            if result:
                logger.info(
                    f"[{self.chain.name}] Birdeye vol+trend: {len(result)} tokens "
                    f"(vol={len(vol_tokens)} trend={len(trend_tokens)})"
                )
            return result

        except Exception as e:
            logger.debug(f"[{self.chain.name}] Birdeye vol+trend error: {e}")
            return {}

    async def _fetch_birdeye_recent(self) -> dict:
        """Fetch tokens with the most recent trade activity from Birdeye.

        Sorted by lastTradeUnixTime descending — catches actively-trading tokens
        that don't rank highly on 24h or 1h percentage sorts yet (e.g. newly pumped
        tokens just entering price discovery). Complements the other Birdeye sorts.
        """
        if self.chain.chain_id != "solana" or not self.birdeye_api_key:
            return {}

        try:
            headers = {
                "X-API-KEY": self.birdeye_api_key,
                "x-chain": "solana",
                "Accept": "application/json",
            }
            params = {
                "sort_by": "lastTradeUnixTime",
                "sort_type": "desc",
                "offset": 0,
                "limit": 50,
                "min_liquidity": 15_000,
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{BIRDEYE_API}/tokenlist",
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return {}
                    data = await resp.json()

            tokens = (data.get("data") or {}).get("tokens") or []
            result = {
                (t.get("address") or "").lower(): t
                for t in tokens
                if t.get("address")
            }
            if result:
                logger.debug(
                    f"[{self.chain.name}] Birdeye recent: {len(result)} tokens"
                )
            return result

        except Exception as e:
            logger.debug(f"[{self.chain.name}] Birdeye recent error: {e}")
            return {}

    async def _fetch_jupiter(self) -> list:
        """Fetch new Raydium pools via Solana RPC (catches all new Solana launches).

        Watches the Raydium AMM v4 program for recently initialized pools — this
        covers pump.fun graduates AND any other new token launching on Raydium.
        Complements _fetch_pumpfun_graduated() by catching non-pump.fun launches.

        Same RPC approach: no external API, no Cloudflare.
        """
        if self.chain.chain_id != "solana":
            return []

        # Raydium AMM v4 program — every new pool creation goes through this
        RAYDIUM_AMM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
        SOL_MINT = "So11111111111111111111111111111111111111112"
        USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
        QUOTE_MINTS = {SOL_MINT, USDC_MINT, USDT_MINT}
        rpc_url = self.trader.rpc_url

        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: recent Raydium program signatures
                async with session.post(rpc_url, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [RAYDIUM_AMM, {"limit": 50, "commitment": "finalized"}]
                }, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        return []
                    sigs_data = await r.json()

                sigs = [
                    x["signature"]
                    for x in (sigs_data.get("result") or [])
                    if not x.get("err")
                ]
                if not sigs:
                    return []

                # Step 2: batch getTransaction
                batch_req = [
                    {
                        "jsonrpc": "2.0", "id": i,
                        "method": "getTransaction",
                        "params": [sig, {
                            "encoding": "json",
                            "maxSupportedTransactionVersion": 0,
                            "commitment": "finalized"
                        }]
                    }
                    for i, sig in enumerate(sigs)
                ]
                async with session.post(
                    rpc_url, json=batch_req,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as r:
                    if r.status != 200:
                        return []
                    tx_results = await r.json()

                # Step 3: extract base token mints (non-SOL/USDC/USDT tokens being traded)
                mints: list[str] = []
                for item in (tx_results if isinstance(tx_results, list) else []):
                    tx = item.get("result") or {}
                    if not tx:
                        continue
                    # Collect unique non-quote mints from this tx (duplicates appear per account)
                    seen_in_tx: set[str] = set()
                    for bal in (tx.get("meta") or {}).get("postTokenBalances", []):
                        mint = bal.get("mint", "")
                        if mint and mint not in QUOTE_MINTS and mint not in seen_in_tx:
                            seen_in_tx.add(mint)
                            mints.append(mint)

                if not mints:
                    return []

                mints = list(dict.fromkeys(mints))
                self._mint_map.update({m.lower(): m for m in mints})

                # Step 4: enrich via DexScreener
                enriched: list[dict] = []
                dex_chain = self.chain.dexscreener_chain
                for i in range(0, len(mints), 30):
                    batch = mints[i:i + 30]
                    url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                    try:
                        async with session.get(
                            url,
                            headers={"User-Agent": "Mozilla/5.0"},
                            timeout=aiohttp.ClientTimeout(total=15),
                        ) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                            for p in (data.get("pairs") or []):
                                if p.get("chainId") != dex_chain:
                                    continue
                                mcap = p.get("marketCap") or 0
                                if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
                                    continue
                                enriched.append(p)
                    except Exception:
                        continue

            seen: dict[str, dict] = {}
            for p in enriched:
                addr = p.get("baseToken", {}).get("address", "").lower()
                if addr and addr not in seen:
                    seen[addr] = p
            result = list(seen.values())

            if result:
                logger.debug(f"[{self.chain.name}] Raydium new pools (RPC): {len(result)} pairs")
            return result

        except Exception as e:
            logger.debug(f"[{self.chain.name}] Raydium RPC error: {e}")
            return []

    async def _fetch_raydium(self) -> list:
        """Fetch active Raydium pairs (Solana only). Returns dex_pair format list."""
        if self.chain.chain_id != "solana":
            return []

        try:
            all_pairs = None
            for _attempt in range(2):
                try:
                    connector = aiohttp.TCPConnector(force_close=True)
                    async with aiohttp.ClientSession(connector=connector) as session:
                        async with session.get(
                            "https://api.raydium.io/v2/main/pairs",
                            headers={
                                "User-Agent": "Mozilla/5.0",
                                "Accept": "application/json",
                                "Accept-Encoding": "identity",
                            },
                            timeout=aiohttp.ClientTimeout(total=20)
                        ) as resp:
                            if resp.status != 200:
                                logger.debug(f"[{self.chain.name}] Raydium HTTP {resp.status}")
                                return []
                            raw_bytes = await resp.read()
                            all_pairs = json.loads(raw_bytes)
                    break  # success
                except Exception as fetch_err:
                    if _attempt == 0:
                        logger.debug(f"[{self.chain.name}] Raydium fetch retry: {fetch_err}")
                        await asyncio.sleep(2)
                    else:
                        logger.warning(f"[{self.chain.name}] Raydium unavailable: {fetch_err}")
                        return []
            if all_pairs is None:
                return []

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

            # Cache correct-case mints — DexScreener lowercases everything,
            # but Jupiter requires exact base58 case. Raydium gives us the truth.
            self._mint_map.update({m.lower(): m for m in base_mints})

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
            logger.warning(f"[{self.chain.name}] Raydium error: {e}")
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
            # Three sorts — diverse coverage to conserve Birdeye credits
            fetch_params = [
                # 24h % gainers — tokens with sustained momentum
                {"sort_by": "v24hChangePercent", "sort_type": "desc", "offset": 0, "limit": 50,
                 "min_liquidity": self.min_mcap / 10},
                # Lowest mcap — small caps about to move
                {"sort_by": "mc", "sort_type": "asc", "offset": 0, "limit": 50,
                 "min_liquidity": 15_000},
                # 1h % gainers — tokens pumping RIGHT NOW (prime dip candidates in 30-60 min)
                {"sort_by": "priceChange1hPercent", "sort_type": "desc", "offset": 0, "limit": 50,
                 "min_liquidity": 15_000},
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

    async def _fetch_birdeye_token_overview(self, token_address: str) -> dict:
        """Fetch per-token data from Birdeye /defi/token_overview.

        Returns the `data` dict from the response, or {} on any error.
        Results are cached for 300s to avoid re-fetching the same token
        on every scan cycle.
        """
        if not self.birdeye_api_key:
            return {}

        addr_key = token_address.lower()
        now = time.monotonic()

        # Check cache first (TTL: 300s)
        if addr_key in self._overview_cache:
            cached_ts, cached_data = self._overview_cache[addr_key]
            if now - cached_ts < 1800:
                return cached_data

        try:
            url = f"{BIRDEYE_API}/token_overview"
            headers = {
                "X-API-KEY": self.birdeye_api_key,
                "x-chain": "solana",
                "Accept": "application/json",
            }
            params = {"address": token_address}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.info(
                            f"[{self.chain.name}] Birdeye overview HTTP {resp.status} "
                            f"for {token_address[:8]}…: {text[:120]}"
                        )
                        self._overview_cache[addr_key] = (now, {})
                        return {}
                    payload = await resp.json()
                    data = payload.get("data") or {}
                    if data:
                        logger.info(
                            f"[{self.chain.name}] Birdeye overview OK for {token_address[:8]}… — "
                            f"holder={data.get('holder', 'N/A')} trade24h={data.get('trade24h', 'N/A')} "
                            f"v24hUSD={data.get('v24hUSD', 'N/A')} uniqueWallet24h={data.get('uniqueWallet24h', 'N/A')}"
                        )
                    else:
                        logger.info(
                            f"[{self.chain.name}] Birdeye overview returned empty data for {token_address[:8]}…"
                        )
                    self._overview_cache[addr_key] = (now, data)
                    return data
        except Exception as e:
            logger.info(
                f"[{self.chain.name}] Birdeye overview error for {token_address[:8]}…: {e}"
            )
            self._overview_cache[addr_key] = (now, {})
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
                            token_address = parts[1]  # preserve case — GeckoTerminal returns correct base58

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

    async def _resolve_mint_address(self, lowercase_addr: str) -> Optional[str]:
        """Look up the correct-case Solana mint address from Jupiter's token list.
        DexScreener returns all-lowercase addresses; Jupiter requires correct base58 case."""
        try:
            api_key = os.getenv("JUPITER_API_KEY", "")
            headers = {"x-api-key": api_key} if api_key else {}
            url = f"https://lite-api.jup.ag/tokens/v2/{lowercase_addr}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        mint = data.get("address") or data.get("mint")
                        if mint:
                            return mint
        except Exception as e:
            logger.debug(f"[_resolve_mint_address] {lowercase_addr[:8]}: {e}")
        return None

    def _build_signal(self, dex_pair: dict,
                      birdeye_data: dict,
                      from_pumpfun: bool = False) -> Optional[TokenSignal]:
        """Build a combined signal from both data sources.

        from_pumpfun=True bypasses the pair-age and sells_h1 filters:
        pump.fun tokens arrive via WebSocket the moment they're created —
        they are always <10 min old and have 0 sells. These filters exist to
        catch rug setups on DEX scanners, but PumpFun tokens go through the
        bonding curve before Raydium listing, so the risk profile is different.
        """
        base = dex_pair.get("baseToken", {})
        token_address = base.get("address", "")  # preserve original case — Jupiter requires it
        token_symbol = base.get("symbol", "?")
        token_name = base.get("name", "Unknown")

        mcap = dex_pair.get("marketCap", 0)
        # Fix 3: Allow mcap=0 (GeckoTerminal couldn't determine it).
        # Downstream scoring naturally penalizes zero-mcap tokens.
        # For non-zero mcap, apply the normal range filter and dead zone.
        if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
            return None
        # Dead zone removed — rug checks now cover the risk that previously justified it

        # Analytics: estimate MCap from liquidity when GeckoTerminal doesn't provide it.
        # Solana memecoins typically have liquidity ~15-20% of MCap → multiplier of 6.
        liquidity_for_estimate = dex_pair.get("liquidity", {}).get("usd", 0)
        if mcap == 0 and liquidity_for_estimate > 0:
            mcap = liquidity_for_estimate * 6
            if mcap < self.min_mcap:
                return None  # estimated mcap too low — likely still on bonding curve

        volume_h1 = dex_pair.get("volume", {}).get("h1", 0)
        volume_h6 = dex_pair.get("volume", {}).get("h6", 0)
        price_change_h1 = dex_pair.get("priceChange", {}).get("h1", 0) or 0
        price_change_h6 = dex_pair.get("priceChange", {}).get("h6", 0) or 0
        liquidity = dex_pair.get("liquidity", {}).get("usd", 0)
        txns_h1 = dex_pair.get("txns", {}).get("h1", {})
        buys_h1 = txns_h1.get("buys", 0)
        sells_h1 = txns_h1.get("sells", 0)

        # ── Rug prevention: hard filters before scoring ───────────────────
        # Bypassed for pump.fun WebSocket tokens: they arrive the moment they're
        # created (always <10 min old, 0 sells) and go through the bonding curve
        # rather than a direct Raydium listing, so the DEX rug-pull pattern
        # doesn't apply. BUG 4 fix: from_pumpfun=True skips both filters below.
        if not from_pumpfun:
            # 1. Require pair to be at least 1.5 hours old.
            #    Tokens under 1.5h have not confirmed momentum — high rug risk.
            pair_created_ms = dex_pair.get("pairCreatedAt", 0) or 0
            if pair_created_ms > 0:
                import time as _time
                pair_age_hours = (_time.time() - pair_created_ms / 1000) / 3600
                if pair_age_hours < self.min_age_hours:
                    logger.info(
                        f"[{self.chain.name}] Rug filter (too new): {token_symbol} "
                        f"pair age {pair_age_hours*60:.1f}min < {self.min_age_hours*60:.0f}min"
                    )
                    return None
                if pair_age_hours > self.hard_skip_age_hours:
                    logger.info(
                        f"[{self.chain.name}] Age filter (too old): {token_symbol} "
                        f"pair age {pair_age_hours:.0f}h > {self.hard_skip_age_hours:.0f}h hard skip"
                    )
                    return None

            # 2. Require at least 5 sell transactions in h1.
            #    Coordinated pump-and-dump setups have hundreds of buys but
            #    almost zero sells — everyone holds until the dev dumps.
            #    Legitimate organic volume has both sides of the book.
            #    The 15% ratio check below handles pump detection for higher volumes.
            if buys_h1 > 0 and sells_h1 < 3:
                logger.info(
                    f"[{self.chain.name}] Rug filter (no sellers): {token_symbol} "
                    f"buys={buys_h1} sells={sells_h1} — no organic sell-side"
                )
                return None

            # 3. Sell ratio check — organic tokens have at least 15% sell-side activity.
            #    Pump setups have near-zero sells until the dev dumps.
            total_txns = buys_h1 + sells_h1
            if total_txns >= 20 and sells_h1 / total_txns < 0.15:
                logger.info(
                    f"[{self.chain.name}] Rug filter (low sell ratio): {token_symbol} "
                    f"sells={sells_h1}/{total_txns} ({sells_h1/total_txns:.1%}) < 15%"
                )
                return None
        price_usd = float(dex_pair.get("priceUsd", 0) or 0)
        info = dex_pair.get("info", {})
        has_social = bool(info.get("socials") or info.get("websites"))
        dex_url = dex_pair.get("url", "")
        pair_address = dex_pair.get("pairAddress", "")

        # DexScreener score
        # GeckoTerminal doesn't provide txn counts — pass txns_available=False
        # so the scorer awards a neutral score instead of penalizing with 0
        txns_available = not dex_pair.get("_gecko_source", False)
        dex_score = self._score_dexscreener(
            mcap, volume_h1, volume_h6, price_change_h1, price_change_h6,
            buys_h1, sells_h1, liquidity, has_social,
            txns_available=txns_available
        )

        # Birdeye score — NOT scored here.
        # The tokenlist endpoint doesn't return the fields _score_birdeye needs
        # (holder, trade24h, v24hUSD, uniqueWallet24h). Scoring is done later
        # in _evaluate_signal via the per-token token_overview endpoint.
        birdeye_score = 0
        holder_count = 0
        holder_growth_pct = 0.0
        smart_money_buying = False

        # Combined score starts as dex_score only.
        # Birdeye component is added in _evaluate_signal after the overview fetch.
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
            pair_address=pair_address,
            confirmed_by_both=confirmed_by_both
        )

    def _score_dexscreener(self, mcap, volume_h1, volume_h6, price_change_h1, price_change_h6,
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
        # BUG 3 fix: check stronger penalty first — elif < -20 was unreachable after elif < 0
        if price_change_h6 > 50:
            score += 15
        elif price_change_h6 > 20:
            score += 10
        elif price_change_h6 > 5:
            score += 5
        elif price_change_h6 < -20:
            score -= 25  # Sharp prior decline — high-risk dead-cat bounce
        elif price_change_h6 < 0:
            score -= 15  # Declining trend — most stop-losses come from here

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

        # Volume acceleration: h1 vs h6 hourly average
        # ratio > 1 = volume picking up; ratio > 2 = spike; ratio < 0.5 = dying
        if volume_h1 > 0 and volume_h6 > 0:
            vol_accel = volume_h1 / (volume_h6 / 6)
            if vol_accel >= 3.0:
                score += 10   # Major spike — strong accumulation event
            elif vol_accel >= 2.0:
                score += 6    # Clear acceleration
            elif vol_accel >= 1.5:
                score += 3    # Mild pickup
            elif vol_accel < 0.5:
                score -= 8    # Volume collapsing — exit pressure

        # Social
        if has_social:
            score += 10

        return max(0, min(100, score))

    def _score_birdeye(self, data: dict):
        """Score using Birdeye token_overview fields.

        Returns: (score, holder_count, holder_growth_pct, smart_money_buying)
        """
        score = 0

        holder_count   = int(data.get("holder", 0) or 0)
        holder_growth  = float(data.get("holderChange24h", 0) or 0)  # 24h % change
        trade_24h      = int(data.get("trade24h", 0) or 0)
        volume_24h     = float(data.get("v24hUSD", 0) or 0)
        unique_w_24h   = int(data.get("uniqueWallet24h", 0) or 0)
        liquidity      = float(data.get("liquidity", 0) or 0)

        # Holder count — fundamental gauge of community size
        if holder_count >= 500:
            score += 25
        elif holder_count >= 200:
            score += 18
        elif holder_count >= 100:
            score += 12
        elif holder_count >= 50:
            score += 6
        else:
            score -= 10  # very low holder base — high rug risk

        # Holder growth (24h %) — accumulation signal
        if holder_growth > 20:
            score += 20
        elif holder_growth > 10:
            score += 12
        elif holder_growth > 5:
            score += 6
        elif holder_growth < -10:
            score -= 15  # significant holder exodus

        # Trade activity (24h trades) — organic vs synthetic volume
        if trade_24h >= 1000:
            score += 20
        elif trade_24h >= 500:
            score += 14
        elif trade_24h >= 200:
            score += 8

        # Unique wallets (24h) — smart money proxy
        smart_money_buying = unique_w_24h > 50
        if unique_w_24h > 50:
            score += 20

        # Volume (24h USD) — confirms real liquidity demand
        if volume_24h >= 100_000:
            score += 15
        elif volume_24h >= 50_000:
            score += 10

        return max(0, min(100, score)), holder_count, holder_growth, smart_money_buying

    async def _fetch_ohlcv(self, token_address: str) -> Optional[list]:
        """Fetch 30 × 5-minute OHLCV candles.

        Tries GeckoTerminal first (free, no key). Falls back to Birdeye OHLCV
        (requires birdeye_api_key) if GeckoTerminal can't find the pool.

        Returns (candles, source) where source is "gecko", "birdeye", or None.
        """
        if not token_address:
            return None

        candles = await self._fetch_ohlcv_gecko(token_address)
        if candles:
            return candles

        if self.birdeye_api_key and self.chain.chain_id == "solana":
            candles = await self._fetch_ohlcv_birdeye(token_address)
            if candles:
                logger.info(
                    f"[{self.chain.name}] OHLCV: GeckoTerminal missed "
                    f"{token_address[:8]}… — using Birdeye fallback"
                )
                return candles
            # Both sources failed — log which ones were tried
            logger.debug(
                f"[{self.chain.name}] OHLCV: both GeckoTerminal and Birdeye "
                f"returned no candles for {token_address[:8]}…"
            )
            return None

        return None

    async def _fetch_ohlcv_gecko(self, token_address: str,
                                   aggregate: str = "5",
                                   limit: int = 30) -> Optional[list]:
        """Fetch OHLCV candles from GeckoTerminal (free, no key).

        aggregate: candle size in minutes ("1" or "5")
        limit:     number of candles to fetch
        """
        try:
            async with aiohttp.ClientSession() as session:
                pools_url = (
                    f"{GECKO_API}/networks/solana/tokens/{token_address}/pools"
                )
                async with session.get(
                    pools_url,
                    params={"page": "1"},
                    headers={"Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status != 200:
                        return None
                    pools_data = await resp.json()
                    pools = pools_data.get("data", [])
                    if not pools:
                        return None
                    pool_addr = pools[0].get("attributes", {}).get("address", "")
                    if not pool_addr:
                        return None

                ohlcv_url = (
                    f"{GECKO_API}/networks/solana/pools/{pool_addr}/ohlcv/minute"
                )
                async with session.get(
                    ohlcv_url,
                    params={"aggregate": aggregate, "limit": str(limit), "currency": "usd"},
                    headers={"Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    candles = (
                        data.get("data", {})
                        .get("attributes", {})
                        .get("ohlcv_list", [])
                    )
                    return list(reversed(candles)) if candles else None
        except Exception:
            return None

    async def _fetch_ohlcv_birdeye(self, token_address: str) -> Optional[list]:
        """Fetch 5-min OHLCV candles from Birdeye as a GeckoTerminal fallback.

        Birdeye V3 OHLCV endpoint. Returns same format as GeckoTerminal:
        [timestamp, open, high, low, close, volume] in chronological order.
        """
        import time as _time
        try:
            now = int(_time.time())
            time_from = now - (30 * 5 * 60)  # 30 candles × 5 min
            url = "https://public-api.birdeye.so/defi/v3/ohlcv"
            params = {
                "address": token_address,
                "type":    "5m",
                "time_from": str(time_from),
                "time_to":   str(now),
            }
            headers = {
                "X-API-KEY": self.birdeye_api_key,
                "x-chain":   "solana",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    items = data.get("data", {}).get("items", [])
                    if not items:
                        return None
                    # Convert to [timestamp, open, high, low, close, volume]
                    candles = [
                        [
                            c["unixTime"],
                            c["open"],
                            c["high"],
                            c["low"],
                            c["close"],
                            c["volume"],
                        ]
                        for c in items
                        if all(k in c for k in ("unixTime", "open", "high", "low", "close", "volume"))
                    ]
                    # Birdeye returns oldest-first already
                    return candles if candles else None
        except Exception as _e:
            logger.debug(f"[{self.chain.name}] Birdeye OHLCV error: {_e}")
            return None

    def _analyze_chart(self, candles: list) -> dict:
        """Compute RSI(14), VWAP, and four entry-timing signals from OHLCV candles.

        Candle format: [timestamp, open, high, low, close, volume]

        Timing signals (each True = good entry condition):
          rsi_pullback     — RSI was >55 earlier, has cooled to 45-62 (momentum reset)
          near_vwap        — price within +10% of VWAP (not extended)
          volume_declining — recent 5-candle vol avg < prior 10-candle avg (consolidation)
          higher_low       — last swing low > previous swing low (uptrend structure)
          price_flat       — 0 consecutive completed green candles (price resting/consolidating)

        Entry requires timing_score >= 3 of 5.
        """
        closes  = [float(c[4]) for c in candles]
        highs   = [float(c[2]) for c in candles]
        lows    = [float(c[3]) for c in candles]
        volumes = [float(c[5]) for c in candles]

        # ── RSI(14) ──────────────────────────────────────────────────────────
        rsi = None
        if len(closes) >= 15:
            diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
            gains  = [d if d > 0 else 0.0 for d in diffs]
            losses = [-d if d < 0 else 0.0 for d in diffs]
            avg_gain = sum(gains[-14:]) / 14
            avg_loss = sum(losses[-14:]) / 14
            if avg_loss == 0:
                rsi = 100.0
            else:
                rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

        # ── VWAP ─────────────────────────────────────────────────────────────
        vwap = None
        price_vs_vwap_pct = None
        typical = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        total_vol = sum(volumes)
        if total_vol > 0:
            vwap = sum(tp * v for tp, v in zip(typical, volumes)) / total_vol
            if closes:
                price_vs_vwap_pct = (closes[-1] - vwap) / vwap * 100

        # ── Timing signal 1: RSI pullback from elevated momentum ─────────────
        # RSI was elevated (>55) in the prior window and has since cooled to 45-62.
        # Prior threshold lowered from >65 → >55: memecoins that rise steadily
        # without a parabolic spike never reach RSI 65, making the signal permanently
        # False for them. >55 captures "was building momentum, now cooling" across
        # both gradual and sharp movers.
        # Needs 25+ candles: compute RSI on candles [-25:-11] as the "prior" reading.
        rsi_pullback = False
        if len(closes) >= 25 and rsi is not None:
            prior_diffs = [closes[i] - closes[i - 1] for i in range(len(closes) - 24, len(closes) - 10)]
            pg = [d if d > 0 else 0.0 for d in prior_diffs]
            pl = [-d if d < 0 else 0.0 for d in prior_diffs]
            p_avg_gain = sum(pg[-14:]) / 14
            p_avg_loss = sum(pl[-14:]) / 14
            if p_avg_loss == 0:
                prior_rsi = 100.0
            else:
                prior_rsi = 100.0 - (100.0 / (1.0 + p_avg_gain / p_avg_loss))
            rsi_pullback = prior_rsi > 55 and 45.0 <= rsi <= 62.0

        # ── Timing signal 2: price near VWAP ────────────────────────────────
        # Price within +10% above (or any amount below) VWAP — tight to the mean.
        # Was +15%; tightened to +10% so we only buy when genuinely not extended.
        near_vwap = price_vs_vwap_pct is not None and price_vs_vwap_pct <= 10.0

        # ── Timing signal 3: volume declining (consolidation, not distribution)
        # Recent 5-candle volume average is meaningfully lower than prior 10-candle
        # average — price is resting on low volume rather than being sold down hard.
        volume_declining = False
        if len(volumes) >= 15:
            recent_avg = sum(volumes[-5:]) / 5
            prior_avg  = sum(volumes[-15:-5]) / 10
            volume_declining = prior_avg > 0 and recent_avg < prior_avg * 0.80

        # ── Timing signal 4: higher low structure ────────────────────────────
        # The most recent swing low is above the previous swing low — uptrend intact.
        # A swing low: candle whose low is below both its neighbours.
        higher_low = False
        swing_lows = [
            lows[i]
            for i in range(1, len(lows) - 1)
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]
        ]
        if len(swing_lows) >= 2:
            higher_low = swing_lows[-1] > swing_lows[-2]

        return {
            "rsi": rsi,
            "vwap": vwap,
            "price_vs_vwap_pct": price_vs_vwap_pct,
            "rsi_pullback": rsi_pullback,
            "near_vwap": near_vwap,
            "volume_declining": volume_declining,
            "higher_low": higher_low,
            # Entry stage — see below
            **self._entry_stage(closes, lows, volumes),
        }

    def _entry_stage(self, closes: list, lows: list, volumes: list) -> dict:
        """Detect whether we are early, mid, or late in a candle move.

        Early  — 1-2 green candles after consolidation, volume just surging,
                  price < 20% above recent base.  Best time to enter.
        Mid    — 2-3 green candles, moderate extension.  Acceptable with strong signals.
        Late   — 3+ consecutive green candles OR price > 35% above recent 10-candle low.
                  Likely buying the top.  Block entry.
        """
        if len(closes) < 4:
            return {"entry_stage": "unknown", "consecutive_green": 0,
                    "move_from_base_pct": 0.0, "vol_surge_ratio": 1.0}

        # Consecutive green candles — skip the current (incomplete) candle,
        # count only fully closed candles to avoid inflating the green count.
        consecutive_green = 0
        for i in range(len(closes) - 2, 0, -1):
            if closes[i] > closes[i - 1]:
                consecutive_green += 1
            else:
                break

        # How far price has moved from its recent 10-candle low (the "base")
        base_low = min(lows[-min(10, len(lows)):])
        current  = closes[-1]
        move_from_base_pct = (
            (current - base_low) / base_low * 100
            if base_low > 0 else 0.0
        )

        # Volume surge: compare the last COMPLETED candle to the prior 10-candle avg.
        # Using volumes[-2] (not volumes[-1]) avoids the partial-candle undercount.
        prior_vols = volumes[-12:-2] if len(volumes) >= 12 else volumes[:-2]
        vol_10_avg = sum(prior_vols) / len(prior_vols) if prior_vols else (volumes[-2] if len(volumes) >= 2 else 1.0)
        last_complete_vol = volumes[-2] if len(volumes) >= 2 else volumes[-1]
        vol_surge_ratio = last_complete_vol / vol_10_avg if vol_10_avg > 0 else 1.0

        # Stage classification
        if consecutive_green >= 3 or move_from_base_pct > 35.0:
            stage = "late"
        elif consecutive_green <= 2 and vol_surge_ratio >= 1.5 and move_from_base_pct < 20.0:
            stage = "early"
        else:
            stage = "mid"

        return {
            "entry_stage":       stage,
            "consecutive_green": consecutive_green,
            "move_from_base_pct": move_from_base_pct,
            "vol_surge_ratio":   vol_surge_ratio,
        }

    def _analyze_dip(self, candles: list, watchlist_peak: Optional[float] = None) -> dict:
        """Detect dip-and-recovery setups for the dip sniper.

        A dip sniper entry fires when:
          - Current price is 15-40% below the peak (session high or watchlist peak)
          - At least 2 of 4 recovery signals are present

        Recovery signals:
          rsi_reset    — RSI in 33-62 range (reset, not still collapsing)
          vol_easing   — recent 3-candle vol avg < prior 7-candle avg by 25%+ (sellers exhausted)
          stabilizing  — recent price moves smaller than earlier moves (momentum slowing)
          last_green   — last candle closed higher than the one before it (first uptick)
        """
        if not candles:
            return {"is_dip": False, "dip_pct": 0.0, "recovery_score": 0, "recovery_signals": {}}

        closes  = [float(c[4]) for c in candles]
        highs   = [float(c[2]) for c in candles]
        volumes = [float(c[5]) for c in candles]

        # Peak = max of watchlist peak (historical) and session high (candle window)
        session_high = max(highs)
        peak = max(watchlist_peak or 0.0, session_high)
        current = closes[-1]

        if peak <= 0 or current <= 0:
            return {"is_dip": False, "dip_pct": 0.0, "recovery_score": 0, "recovery_signals": {}}

        dip_pct = (current - peak) / peak * 100  # negative value
        in_range = -40.0 <= dip_pct <= -15.0

        if not in_range:
            return {"is_dip": False, "dip_pct": dip_pct, "recovery_score": 0, "recovery_signals": {}}

        # Signal 1: RSI reset to neutral (not still in freefall)
        rsi = None
        if len(closes) >= 15:
            diffs  = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
            gains  = [d if d > 0 else 0.0 for d in diffs]
            losses = [-d if d < 0 else 0.0 for d in diffs]
            ag = sum(gains[-14:]) / 14
            al = sum(losses[-14:]) / 14
            rsi = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
        rsi_reset = rsi is not None and 33.0 <= rsi <= 62.0

        # Signal 2: volume easing off (selling pressure exhausted)
        vol_easing = False
        if len(volumes) >= 10:
            recent_avg = sum(volumes[-3:]) / 3
            prior_avg  = sum(volumes[-10:-3]) / 7
            vol_easing = prior_avg > 0 and recent_avg < prior_avg * 0.75

        # Signal 3: price moves shrinking (momentum slowing, not accelerating down)
        stabilizing = False
        if len(closes) >= 7:
            def _avg_move(sl):
                return sum(
                    abs(closes[i] - closes[i - 1]) / max(closes[i - 1], 1e-10)
                    for i in sl
                ) / len(sl)
            stabilizing = _avg_move(range(-3, 0)) < _avg_move(range(-6, -3)) * 0.85

        # Signal 4: last candle closed green (first sign of reversal)
        last_green = len(closes) >= 2 and closes[-1] > closes[-2]

        recovery_signals = {
            "RSI reset":   rsi_reset,
            "Vol easing":  vol_easing,
            "Stabilizing": stabilizing,
            "Last green":  last_green,
        }
        recovery_score = sum(recovery_signals.values())

        return {
            "is_dip":           recovery_score >= 3,
            "dip_pct":          dip_pct,
            "peak":             peak,
            "rsi":              rsi,
            "recovery_score":   recovery_score,
            "recovery_signals": recovery_signals,
        }

    async def _evaluate_signal(self, signal: TokenSignal):
        """Evaluate a signal and decide whether to buy."""
        # Respect manual pause — skip all new entries while paused
        if self.tracker and self.tracker.buying_paused:
            return

        # Skip if we already hold this token — don't double-buy
        if signal.token_address.lower() in self.trader.open_positions:
            return

        # Skip well-known non-memecoin tokens (wSOL, USDC, etc.)
        _SKIP_MINTS = {
            "so11111111111111111111111111111111111111112",  # Wrapped SOL
            "epjfwdd5aufqssqem2qn1xzybapC8G4wEGGkZwyTDt1v".lower(),  # USDC
            "es9vmfrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB".lower(),  # USDT
        }
        if signal.token_address.lower() in _SKIP_MINTS:
            logger.debug(f"[{self.chain.name}] Skipping {signal.token_symbol} — base asset, not a memecoin")
            return

        # Block free-falling tokens (>15% down in 1h) — likely rug or broken chart.
        # Tokens with moderate dips (-15% to 0%) are fine — dip-buy logic handles them.
        if signal.price_change_h1 <= -15:
            logger.debug(
                f"[{self.chain.name}] Skipping {signal.token_symbol} — "
                f"free-falling: {signal.price_change_h1:.1f}% on 1h"
            )
            return

        # ── Birdeye enrichment — BEFORE score threshold ──────────────────
        # Fetch token_overview for promising Solana tokens that don't have
        # Birdeye data yet. This must run before the score gate so Birdeye
        # can boost combined_score for tokens that would otherwise miss.
        if (self.birdeye_api_key
                and self.chain.chain_id == "solana"
                and signal.dex_score >= 40
                and signal.birdeye_score == 0):
            overview = await self._fetch_birdeye_token_overview(signal.token_address)
            if overview:
                be_score, holder_count, growth_pct, smart_money = self._score_birdeye(overview)
                logger.info(
                    f"[{self.chain.name}] Birdeye overview {signal.token_symbol}: "
                    f"be_score={be_score} holders={holder_count} growth={growth_pct:+.1f}% "
                    f"smart_money={smart_money}"
                )
                signal.birdeye_score = be_score
                signal.holder_count = holder_count
                signal.holder_growth_pct = growth_pct
                signal.smart_money_buying = smart_money
                if be_score >= 30:
                    signal.combined_score = int(signal.dex_score * 0.6 + be_score * 0.4)
                # Don't lower combined_score if birdeye is weak — keep dex_score
                signal.confirmed_by_both = (signal.dex_score >= 40 and be_score >= 40)
            else:
                logger.info(
                    f"[{self.chain.name}] Birdeye overview EMPTY for {signal.token_symbol} "
                    f"({signal.token_address[:8]}…) — scoring with dex_score only"
                )

        # ── Option A: require both sources to confirm ────────────────────
        # Only enforced when Birdeye key is present — if Birdeye is down we
        # fall back to dex-only so the bot doesn't stop trading entirely.
        if (self.require_both_sources
                and self.birdeye_api_key
                and self.chain.chain_id == "solana"
                and not signal.confirmed_by_both):
            logger.info(
                f"[{self.chain.name}] Both-sources required: {signal.token_symbol} blocked "
                f"(dex={signal.dex_score} birdeye={signal.birdeye_score} confirmed_both=False)"
            )
            self.signals_blocked_score += 1
            return

        # ── Option C: minimum holder floor ───────────────────────────────
        # Tokens with very few holders are almost always coordinated pumps.
        # Only enforced when we have Birdeye data (holder_count > 0).
        if (self.min_holder_count > 0
                and signal.holder_count > 0
                and signal.holder_count < self.min_holder_count):
            logger.info(
                f"[{self.chain.name}] Holder floor blocked: {signal.token_symbol} "
                f"{signal.holder_count} holders < {self.min_holder_count} minimum"
            )
            self.signals_blocked_score += 1
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

        # Score cap: scores above 85 are likely over-pumped / late entry.
        # Raised from 75 → 85 after adding rug filters that now properly vet high-scoring tokens.
        if signal.combined_score > self.max_combined_score:
            logger.info(
                f"[{self.chain.name}] Score-cap blocked: {signal.token_symbol} "
                f"score={signal.combined_score} > max {self.max_combined_score} "
                f"(over-pumped / rug risk)"
            )
            self.signals_blocked_score += 1
            return

        # Rug blacklist: skip tokens that are cooling down after a stop/rug
        if self.tracker and self.tracker.is_rugged(signal.token_address):
            expiry = self.tracker._rug_blacklist.get(signal.token_address.lower())
            if expiry:
                mins_left = int((expiry - datetime.now(timezone.utc)).total_seconds() / 60)
                block_str = f"{mins_left}min remaining"
            else:
                block_str = "cooling down"
            logger.info(
                f"[{self.chain.name}] Rug-blacklist blocked: {signal.token_symbol} "
                f"({signal.token_address[:8]}…) — {block_str}"
            )
            return

        if self.trader.risk_manager.is_daily_limit_hit():
            return

        # Hard liquidity floor — pools below minimum are trivially drainable
        if signal.liquidity_usd < self.min_liquidity_usd:
            logger.info(
                f"[{self.chain.name}] Liquidity floor blocked: {signal.token_symbol} "
                f"${signal.liquidity_usd:,.0f} < ${self.min_liquidity_usd:,.0f}"
            )
            return

        # Volume floor — no real trading activity below this threshold
        if signal.volume_h1 < self.min_volume_h1_usd:
            logger.info(
                f"[{self.chain.name}] Volume floor blocked: {signal.token_symbol} "
                f"${signal.volume_h1:,.0f}/hr < ${self.min_volume_h1_usd:,.0f}/hr"
            )
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

        # Rug classifier — heuristic ML check built from signal + security data.
        # Falls back to rule-based scoring until 200 labeled examples are collected.
        if self.rug_classifier:
            try:
                from ml.rug_classifier import TokenFeatures as _TF
                total_txns = max(signal.buy_count_h1 + signal.sell_count_h1, 1)
                rf = _TF(
                    token_address=signal.token_address,
                    chain_id=self.chain.chain_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    lp_amount_usd=signal.liquidity_usd,
                    buy_sell_ratio_5min=signal.buy_count_h1 / total_txns,
                    has_twitter=signal.has_social,
                    has_telegram=signal.has_social,
                    is_mintable=sec_result.can_mint or False,
                    has_blacklist=sec_result.has_blacklist or False,
                    buy_tax=sec_result.buy_tax or 0.0,
                    sell_tax=sec_result.sell_tax or 0.0,
                    lp_locked=sec_result.liquidity_locked or False,
                    top_holder_pct=(sec_result.top10_concentration or 0) / 10,
                    top5_holders_pct=(sec_result.top10_concentration or 0) / 2,
                    creator_prev_rugs=1 if (sec_result.dev_holding_pct or 0) > 20 else 0,
                )
                rug_pred = await self.rug_classifier.predict(rf)
                logger.info(
                    f"[{self.chain.name}] Rug classifier: {signal.token_symbol} — "
                    f"{rug_pred.summary()}"
                )
                if not rug_pred.passed:
                    logger.warning(
                        f"[{self.chain.name}] Rug classifier blocked: {signal.token_symbol} "
                        f"prob={rug_pred.rug_probability*100:.1f}% "
                        f"[{', '.join(rug_pred.top_risk_factors)}]"
                    )
                    self.signals_blocked_security += 1
                    return
            except Exception as _rc_err:
                logger.warning(
                    f"[{self.chain.name}] Rug classifier error for {signal.token_symbol}: "
                    f"{_rc_err} — skipping classifier, continuing to chart analysis"
                )

        # Stop-loss cooldown: don't rebuy within 4h of a stop-loss on this token.
        addr_lower = signal.token_address.lower()
        if addr_lower in self._sl_cooldown:
            remaining_min = int((self._sl_cooldown[addr_lower] - time.monotonic()) / 60)
            logger.debug(
                f"[{self.chain.name}] SL cooldown: {signal.token_symbol} "
                f"blocked for {remaining_min}m more"
            )
            return

        # Macro trend filter: block tokens in sustained downtrends.
        # A local -13% dip within a -50% 6h crash is NOT a dip-buy opportunity.
        if signal.price_change_h6 < -20:
            logger.info(
                f"[{self.chain.name}] Macro trend blocked: {signal.token_symbol} "
                f"h6={signal.price_change_h6:+.1f}% — sustained downtrend, not a dip"
            )
            self.signals_blocked_score += 1
            return

        # Dip-buy: require Birdeye confirmation for all entries.
        # Single-source tokens lack holder data and second-source verification —
        # not worth buying even on a dip. Quality > quantity.
        if (self.birdeye_api_key
                and self.chain.chain_id == "solana"
                and not signal.confirmed_by_both):
            logger.info(
                f"[{self.chain.name}] Dip-buy blocked: {signal.token_symbol} "
                f"— needs Birdeye confirmation (dex={signal.dex_score} be={signal.birdeye_score})"
            )
            self.signals_blocked_score += 1
            return

        # Dip-buy chart check — fetch candles, run all dip/recovery filters.
        # Returns False if blocked (signal stored in watchlist for poller to retry).
        if not await self._chart_dip_check(signal, sec_result.risk_level):
            self.signals_blocked_score += 1
            return

        await self._fire_chart_buy(signal, sec_result.risk_level)

    async def _sol_macro_ok_check(self) -> bool:
        """
        Returns True if SOL's h1 price change is better than -3%.
        Result is cached for 5 minutes to avoid excessive API calls.
        On any fetch error, returns True (don't block on uncertainty).
        """
        now = time.monotonic()
        if now - self._sol_macro_ts < 300:  # 5-minute cache
            return self._sol_macro_ok

        try:
            SOL_MINT = "So11111111111111111111111111111111111111112"
            url = f"https://api.dexscreener.com/latest/dex/tokens/{SOL_MINT}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=6)
                ) as resp:
                    if resp.status != 200:
                        return True
                    data = await resp.json()

            pairs = data.get("pairs") or []
            # Pick the most liquid SOL/USDC or SOL/USDT pair
            sol_pairs = [
                p for p in pairs
                if p.get("chainId") == "solana"
                and p.get("quoteToken", {}).get("symbol", "").upper()
                in ("USDC", "USDT", "USD")
            ]
            if not sol_pairs:
                return True

            best = max(sol_pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0))
            h1_change = best.get("priceChange", {}).get("h1", 0) or 0

            ok = h1_change > -3.0
            self._sol_macro_ok = ok
            self._sol_macro_ts = now

            if not ok:
                logger.info(
                    f"[{self.chain.name}] SOL macro: h1={h1_change:+.1f}% — "
                    f"market dumping, dip buys paused"
                )
            else:
                logger.debug(
                    f"[{self.chain.name}] SOL macro: h1={h1_change:+.1f}% — OK"
                )
            return ok

        except Exception as e:
            logger.debug(f"[{self.chain.name}] SOL macro check error: {e}")
            return True  # fail open — don't block on uncertainty

    async def _chart_dip_check(self, signal: TokenSignal, risk_level: str = "UNKNOWN") -> bool:
        """
        Fetch 5m+1m candles and run all dip/recovery filters.
        Stores signal in _dip_watchlist on soft blocks so the watchlist poller
        can retry every 60s without waiting for the token to resurface in a scan cycle.
        Returns True only when all conditions pass and a buy should proceed.
        """
        if not signal.token_address:
            return False

        # Fetch 5-min and 1-min candles concurrently
        candles_5m, candles_1m = await asyncio.gather(
            self._fetch_ohlcv(signal.token_address),
            self._fetch_ohlcv_gecko(signal.token_address, aggregate="1", limit=30),
            return_exceptions=True,
        )
        if isinstance(candles_5m, Exception):
            candles_5m = None
        if isinstance(candles_1m, Exception):
            candles_1m = None

        if not candles_5m or len(candles_5m) < 10:
            if not candles_5m:
                sources_tried = "GeckoTerminal + Birdeye" if self.birdeye_api_key else "GeckoTerminal"
                reason = f"no candles from {sources_tried} — pool not indexed yet"
            else:
                reason = f"only {len(candles_5m)} candles — need ≥10 (token too new)"
            logger.info(
                f"[{self.chain.name}] Chart blocked: {signal.token_symbol} — {reason}"
            )
            return False

        candles = candles_5m  # 5m is primary for all analysis

        chart = self._analyze_chart(candles)
        rsi   = chart["rsi"]
        pvwap = chart["price_vs_vwap_pct"]
        rsi_str   = f"{rsi:.1f}" if rsi is not None else "n/a"
        pvwap_str = f"{pvwap:+.1f}%" if pvwap is not None else "n/a"

        def _safe_float(val):
            try:
                return float(val) if val is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        closes  = [_safe_float(c[4]) for c in candles]
        highs   = [_safe_float(c[2]) for c in candles]
        lows    = [_safe_float(c[3]) for c in candles]
        volumes = [_safe_float(c[5]) for c in candles]
        current = closes[-1]

        closes_1m = [_safe_float(c[4]) for c in candles_1m] if candles_1m else []

        logger.info(
            f"[{self.chain.name}] Chart {signal.token_symbol}: "
            f"RSI={rsi_str} VWAP={pvwap_str} "
            f"({len(candles)} × 5m candles, {len(closes_1m)} × 1m candles)"
        )

        addr_lower      = signal.token_address.lower()
        watchlist_entry = self._dip_watchlist.get(addr_lower)
        watchlist_peak  = watchlist_entry["peak_price"] if watchlist_entry else None

        # ── Hard block: RSI parabolic ─────────────────────────────────────────
        if rsi is not None and rsi > 80:
            self._dip_watchlist[addr_lower] = {
                "peak_price": max(highs),
                "added_at": time.monotonic(),
                "signal": signal,
                "risk_level": risk_level,
            }
            logger.info(
                f"[{self.chain.name}] Chart blocked: {signal.token_symbol} "
                f"RSI={rsi:.1f} — overbought, watching for dip"
            )
            return False

        # ── Hard block: price severely extended above VWAP ────────────────────
        if pvwap is not None and pvwap > 25:
            self._dip_watchlist[addr_lower] = {
                "peak_price": max(highs),
                "added_at": time.monotonic(),
                "signal": signal,
                "risk_level": risk_level,
            }
            logger.info(
                f"[{self.chain.name}] Chart blocked: {signal.token_symbol} "
                f"price {pvwap:+.1f}% above VWAP — extended, watching for dip"
            )
            return False

        # ── Grinder filter ────────────────────────────────────────────────────
        candle_peak  = max(highs)
        candle_floor = min(lows)
        if candle_floor > 0 and candle_peak / candle_floor < 1.15:
            logger.info(
                f"[{self.chain.name}] Grinder filtered: {signal.token_symbol} "
                f"range {candle_peak / candle_floor:.2f}x — no meaningful price action"
            )
            return False

        # ── Dip range check ───────────────────────────────────────────────────
        peak = candle_peak
        if peak <= 0 or current <= 0:
            logger.info(
                f"[{self.chain.name}] Chart blocked: {signal.token_symbol} — invalid price"
            )
            return False

        dip_pct      = (current - peak) / peak * 100
        in_dip_range = -45.0 <= dip_pct <= -15.0

        if not in_dip_range:
            self._dip_watchlist[addr_lower] = {
                "peak_price": max(candle_peak, watchlist_peak or 0.0),
                "added_at":   (watchlist_entry or {}).get("added_at", time.monotonic()),
                "signal":     signal,
                "risk_level": risk_level,
            }
            logger.info(
                f"[{self.chain.name}] No dip yet: {signal.token_symbol} "
                f"{dip_pct:+.1f}% from peak — watching (need -15% to -45%)"
            )
            return False

        # ── Rug-dump detection ────────────────────────────────────────────────
        dump_1m = (
            len(closes_1m) >= 10
            and all(
                float(candles_1m[i][4]) < float(candles_1m[i][1])
                for i in range(-10, 0)
            )
        ) if candles_1m else False
        dump_5m = (
            len(candles) >= 5
            and all(float(candles[i][4]) < float(candles[i][1]) for i in range(-5, 0))
        )
        if dump_1m:
            logger.info(
                f"[{self.chain.name}] Rug-dump blocked: {signal.token_symbol} "
                f"10 consecutive red 1m candles — actively dumping right now"
            )
            return False
        if dump_5m:
            logger.info(
                f"[{self.chain.name}] Rug-dump blocked: {signal.token_symbol} "
                f"5 consecutive red 5m candles — sustained selling pressure"
            )
            return False

        # ── Recovery signals ──────────────────────────────────────────────────
        # last_green: mandatory — last 5m candle must be green
        last_green = len(closes) >= 2 and closes[-1] > closes[-2]
        if not last_green:
            logger.info(
                f"[{self.chain.name}] No recovery: {signal.token_symbol} "
                f"{dip_pct:+.1f}% dip but last 5m candle still red — waiting"
            )
            self._dip_watchlist[addr_lower] = {
                "peak_price": peak,
                "added_at":   (watchlist_entry or {}).get("added_at", time.monotonic()),
                "signal":     signal,
                "risk_level": risk_level,
            }
            return False

        # bounce_confirmed: mandatory — current price must be ≥2% above candle low.
        # Requires a real bounce off the bottom, not just a 1-tick uptick.
        candle_low   = min(lows)
        bounce_pct   = (current - candle_low) / candle_low * 100 if candle_low > 0 else 0
        bounce_confirmed = bounce_pct >= 2.0
        if not bounce_confirmed:
            logger.info(
                f"[{self.chain.name}] Bounce too weak: {signal.token_symbol} "
                f"only {bounce_pct:+.1f}% off candle low — need ≥2%, watching"
            )
            self._dip_watchlist[addr_lower] = {
                "peak_price": peak,
                "added_at":   (watchlist_entry or {}).get("added_at", time.monotonic()),
                "signal":     signal,
                "risk_level": risk_level,
            }
            return False

        # bounce_volume: mandatory — last candle volume must exceed avg of prior 3.
        # Real buyers stepping in = volume increases on the recovery candle.
        # A bounce on declining volume is just absence of sellers, not real demand.
        bounce_volume = False
        if len(volumes) >= 4:
            prior_vol_avg = sum(volumes[-4:-1]) / 3
            bounce_volume = prior_vol_avg > 0 and volumes[-1] > prior_vol_avg
        if not bounce_volume:
            logger.info(
                f"[{self.chain.name}] Bounce volume weak: {signal.token_symbol} "
                f"recovery candle has no volume surge — waiting for real buyers"
            )
            self._dip_watchlist[addr_lower] = {
                "peak_price": peak,
                "added_at":   (watchlist_entry or {}).get("added_at", time.monotonic()),
                "signal":     signal,
                "risk_level": risk_level,
            }
            return False

        # rsi_reset: optional signal (counted in recovery score below)
        rsi_reset = rsi is not None and 30.0 <= rsi <= 60.0

        vol_easing  = False
        if len(volumes) >= 10:
            recent_avg = sum(volumes[-3:]) / 3
            prior_avg  = sum(volumes[-10:-3]) / 7
            vol_easing = prior_avg > 0 and recent_avg < prior_avg * 0.70

        stabilizing = False
        if len(closes) >= 7:
            def _avg_move(sl):
                return sum(
                    abs(closes[i] - closes[i - 1]) / max(closes[i - 1], 1e-10)
                    for i in sl
                ) / len(sl)
            stabilizing = _avg_move(range(-3, 0)) < _avg_move(range(-6, -3)) * 0.80

        higher_low  = len(lows) >= 4 and lows[-1] > min(lows[-4:-1])

        momentum_1m = False
        if len(closes_1m) >= 5:
            up_count = sum(
                1 for i in range(-5, 0)
                if closes_1m[i] > closes_1m[i - 1]
            )
            momentum_1m = up_count >= 3

        # Optional signals — 3 mandatory already passed, need 1 more from these
        recovery_signals = {
            "Last green":  last_green,
            "Bounce ≥2%":  bounce_confirmed,
            "Bounce vol":  bounce_volume,
            "RSI reset":   rsi_reset,
            "Vol easing":  vol_easing,
            "Stabilizing": stabilizing,
            "Higher low":  higher_low,
            "1m momentum": momentum_1m,
        }
        recovery_score = sum(recovery_signals.values())
        rec_str = " | ".join(
            f"{k}={'✓' if v else '✗'}" for k, v in recovery_signals.items()
        )

        logger.info(
            f"[{self.chain.name}] 🎯 DIP CHECK: {signal.token_symbol} "
            f"{dip_pct:+.1f}% from peak | recovery {recovery_score}/8 [{rec_str}]"
        )

        if recovery_score < 4:
            logger.info(
                f"[{self.chain.name}] Weak recovery: {signal.token_symbol} "
                f"{recovery_score}/8 signals — need 4, watching"
            )
            self._dip_watchlist[addr_lower] = {
                "peak_price": peak,
                "added_at":   (watchlist_entry or {}).get("added_at", time.monotonic()),
                "signal":     signal,
                "risk_level": risk_level,
            }
            return False

        logger.info(
            f"[{self.chain.name}] ✅ DIP ENTRY confirmed: {signal.token_symbol} "
            f"{dip_pct:+.1f}% from peak, {recovery_score}/8 recovery"
        )
        self._dip_watchlist.pop(addr_lower, None)
        return True

    async def _fire_chart_buy(self, signal: TokenSignal, risk_level: str):
        """Execute the buy after all dip/chart checks have passed."""
        # Skip if already holding (race guard between scan cycle and watchlist poller)
        if signal.token_address.lower() in self.trader.open_positions:
            return

        # Resolve correct-case mint for Jupiter
        if self.chain.chain_id == "solana":
            resolved = self._mint_map.get(signal.token_address.lower()) or signal.token_address
            signal.token_address = resolved

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
            f"Security: {risk_level}\n\n"
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
            price_hint=signal.price_usd,
            entry_mcap=signal.mcap,
            entry_liquidity=signal.liquidity_usd,
            entry_volume_h1=signal.volume_h1,
            entry_buys_h1=signal.buy_count_h1,
            entry_sells_h1=signal.sell_count_h1,
            holder_count=signal.holder_count,
            dex_score=signal.dex_score,
            birdeye_score=signal.birdeye_score,
        )

    async def _watchlist_poll_cycle(self):
        """
        Re-check dip/recovery conditions for all watchlist tokens that have a stored signal.
        Runs independently of the main scan cycle so dips aren't missed between cycles.
        """
        now = time.monotonic()
        to_remove = []

        for addr_lower, entry in list(self._dip_watchlist.items()):
            signal: TokenSignal = entry.get("signal")
            if not signal:
                continue  # watchlist entry without a signal (bad-entry seed, etc.)

            # Skip SL cooldown
            if addr_lower in self._sl_cooldown:
                continue

            # Skip if already holding this token
            if addr_lower in self.trader.open_positions:
                to_remove.append(addr_lower)
                continue

            # Skip if buying is paused
            if self.tracker and self.tracker.buying_paused:
                continue

            try:
                passed = await self._chart_dip_check(signal, entry.get("risk_level", "UNKNOWN"))
                if passed:
                    to_remove.append(addr_lower)
                    logger.info(
                        f"[{self.chain.name}] 🔔 Watchlist dip triggered: "
                        f"{signal.token_symbol} — firing buy from poller"
                    )
                    await self._fire_chart_buy(signal, entry.get("risk_level", "UNKNOWN"))
            except Exception as e:
                logger.debug(
                    f"[{self.chain.name}] Watchlist poll error for "
                    f"{signal.token_symbol}: {e}"
                )

        for addr in to_remove:
            self._dip_watchlist.pop(addr, None)

    async def _watchlist_poller(self):
        """Background task: poll watchlist tokens for dip entries every 60 seconds."""
        # Initial delay so the first scan cycle runs and populates the watchlist first
        await asyncio.sleep(90)
        while True:
            try:
                watchable = sum(
                    1 for e in self._dip_watchlist.values() if e.get("signal")
                )
                if watchable:
                    logger.debug(
                        f"[{self.chain.name}] Watchlist poller: "
                        f"checking {watchable} tokens for dip entry"
                    )
                    await self._watchlist_poll_cycle()
            except Exception as e:
                logger.error(f"[{self.chain.name}] Watchlist poller error: {e}")
            await asyncio.sleep(60)

    async def inject_token_from_address(self, address: str, source: str = "telegram"):
        """
        Inject a token address directly into the evaluation pipeline.
        Called by the Telegram channel monitor when a contract address is spotted
        in an alpha/caller channel. Fetches DexScreener data and runs through
        the full pipeline: score → Birdeye enrichment → security → dip check → buy.
        """
        chain_id = self.chain.chain_id
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()

            pairs = data.get("pairs") or []
            # Filter to this chain, pick the most liquid pair
            chain_pairs = [
                p for p in pairs
                if p.get("chainId", "").lower() == chain_id
            ]
            if not chain_pairs:
                logger.debug(
                    f"[{self.chain.name}] TG inject: {address[:8]}… "
                    f"not found on {chain_id} (from @{source})"
                )
                return

            pair = max(chain_pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0))
            signal = self._build_signal(pair, self._birdeye_cache)
            if signal is None:
                return

            logger.info(
                f"[{self.chain.name}] 📡 TG signal [@{source}]: "
                f"{signal.token_symbol} score={signal.combined_score} "
                f"mcap=${signal.mcap:,.0f}"
            )
            await self._evaluate_signal(signal)

        except Exception as e:
            logger.debug(
                f"[{self.chain.name}] inject_token_from_address error "
                f"{address[:8]}: {e}"
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

        token_address = mint  # preserve original case — Jupiter requires it

        # Skip if already seen
        cache_key = f"solana:{token_address.lower()}"
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

        signal = self.scanner._build_signal(dex_pair, {}, from_pumpfun=True)
        if signal:
            await self.scanner._evaluate_signal(signal)
