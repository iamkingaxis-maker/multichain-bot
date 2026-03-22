"""
Multi-Source Scanner
Queries DexScreener, GeckoTerminal, Raydium, PumpFun, Jupiter, and SolanaTracker.
Signals a buy when a token scores above the combined threshold.

Data sources:
  DexScreener    — pairs, volume, price change, liquidity (80-keyword rotating pool)
  GeckoTerminal  — new pools, price change, liquidity (no API key needed)
  Raydium        — active Raydium pairs
  PumpFun RPC    — recently graduated pump.fun tokens via Solana RPC
  Jupiter (RPC)  — new Raydium pool launches via Solana RPC
  SolanaTracker  — trending Solana tokens (optional, key-gated)
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
    birdeye_score: int          # Kept for interface compatibility — always 0 (Birdeye removed)
    combined_score: int         # Final combined score
    has_social: bool
    dex_url: str
    confirmed_by_both: bool     # True only if both sources agree
    hh_hl_confirmed: bool = False  # From signal evaluator — HH+HL structure
    pair_address: str = ""          # DEX pool address (used for GeckoTerminal OHLCV)
    flags: List[str] = field(default_factory=list)
    raw_pair_data: dict = field(default_factory=dict)  # Original DexScreener pair data


class MultiSourceScanner:
    """
    Enhanced scanner that cross-references DexScreener, GeckoTerminal, Raydium,
    PumpFun (RPC), Jupiter (RPC), and SolanaTracker.
    Birdeye removed — credits exhausted.
    """

    # Rotating keyword pool for DexScreener search.
    # Each cycle picks 20 keywords rotating through the full pool of ~80.
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

    def __init__(self,
                 chain,
                 trader,
                 security_checker,
                 telegram,
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
                 sentiment_analyzer=None,
                 tracker=None,
                 startup_delay: float = 0,
                 scanner_keywords: List[str] = None):
        self.chain = chain
        self.trader = trader
        self.min_age_hours = preferred_age_min_hours
        self.hard_skip_age_hours = hard_skip_age_hours
        self.rug_classifier = rug_classifier
        self.sentiment_analyzer = sentiment_analyzer
        self.security_checker = security_checker
        self.telegram = telegram
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

        # scanner_keywords: if provided, overrides the rotating pool for keyword search
        self.scanner_keywords = scanner_keywords  # None = use _DEXSCREENER_KEYWORDS rotation

        # LRU-bounded seen_tokens — evict oldest when >500 entries
        self.seen_tokens: set = set()
        self._seen_tokens_order: list = []

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
        self.signals_blocked_age: int = 0

        self._solanatracker_last_fetch: float = 0
        self._solanatracker_cache: list = []

        # Per-token overview cache (not Birdeye — kept for any future enrichment)
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
        self._sol_macro_ts: float = 0
        self._sol_macro_ok: bool = True

        # Dashboard watchlist — near-miss tokens scoring 45-64 (DIFFERENT from _dip_watchlist)
        self.watchlist: Dict[str, dict] = {}
        self._watchlist_max = 20
        self._watchlist_ttl = 7200  # 2 hours in seconds

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
        logger.info(
            f"[{self.chain.name}] Multi-Source Scanner started — "
            f"${self.min_mcap/1000:.0f}k-${self.max_mcap/1000:.0f}k | "
            f"Min score: {self.min_combined_score} | Birdeye: DISABLED"
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
        if self.tracker and hasattr(self.tracker, 'get_bad_entries'):
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

        # SolanaTracker: fetch every 5 minutes
        _ST_INTERVAL = 300
        _st_fresh = False
        if self.solanatracker_api_key and now - self._solanatracker_last_fetch >= _ST_INTERVAL:
            fetch_st_coro = self._fetch_solanatracker()
            _st_fresh = True
        else:
            async def _cached_st(cache=self._solanatracker_cache):
                return cache
            fetch_st_coro = _cached_st()

        # Run all fetches concurrently (no Birdeye)
        (dex_tokens, gecko_tokens, raydium_tokens,
         trending_tokens, pumpfun_tokens,
         jupiter_tokens, st_tokens) = await asyncio.gather(
            self._fetch_dexscreener(),
            self._fetch_geckoterminal(),
            self._fetch_raydium(),
            self._fetch_dexscreener_trending(),
            self._fetch_pumpfun_graduated(),
            self._fetch_jupiter(),
            fetch_st_coro,
            return_exceptions=True
        )

        if _st_fresh and not isinstance(st_tokens, Exception):
            self._solanatracker_cache = st_tokens
            self._solanatracker_last_fetch = time.monotonic()

        if isinstance(dex_tokens, Exception):
            dex_tokens = []
        if isinstance(gecko_tokens, Exception):
            gecko_tokens = {}
        if isinstance(raydium_tokens, Exception):
            raydium_tokens = []
        if isinstance(trending_tokens, Exception):
            trending_tokens = []
        if isinstance(pumpfun_tokens, Exception):
            pumpfun_tokens = []
        if isinstance(jupiter_tokens, Exception):
            jupiter_tokens = []
        if isinstance(st_tokens, Exception):
            st_tokens = []

        logger.info(
            f"[{self.chain.name}] DexScreener: {len(dex_tokens)} | "
            f"GeckoTerminal: {len(gecko_tokens)} | Raydium: {len(raydium_tokens)} | "
            f"PumpFun-RPC: {len(pumpfun_tokens)} | Raydium-RPC: {len(jupiter_tokens)} | "
            f"SolanaTracker: {len(st_tokens)} tokens"
        )

        # Merge native DEX tokens into DexScreener list
        _dex_addr_set: dict = {
            t.get("baseToken", {}).get("address", "").lower(): True
            for t in dex_tokens
        }
        for rt in raydium_tokens + trending_tokens + pumpfun_tokens + jupiter_tokens + st_tokens:
            addr = rt.get("baseToken", {}).get("address", "").lower()
            if addr and addr not in _dex_addr_set:
                dex_tokens.append(rt)
                _dex_addr_set[addr] = True

        # Prune stale dashboard watchlist entries
        self._prune_watchlist()

        # Evaluate DexScreener + merged tokens
        dex_addrs = set()
        for token in dex_tokens:
            try:
                addr = token.get("baseToken", {}).get("address", "").lower()
                if not addr or addr in _cycle_seen:
                    continue
                _cycle_seen.add(addr)
                dex_addrs.add(addr)

                # LRU eviction for seen_tokens
                cache_key = f"{self.chain.chain_id}:{addr}"
                if cache_key not in self.seen_tokens:
                    self.seen_tokens.add(cache_key)
                    self._seen_tokens_order.append(cache_key)
                    if len(self._seen_tokens_order) > 500:
                        old = self._seen_tokens_order.pop(0)
                        self.seen_tokens.discard(old)

                signal = self._build_signal(token, {})
                if signal:
                    await self._evaluate_signal(signal)
            except Exception as e:
                logger.debug(f"[{self.chain.name}] Token eval error: {e}")

        # Evaluate GeckoTerminal-only tokens
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

        # Send scan summary AFTER evaluation so counts are accurate
        await self.telegram.send(
            f"🔍 Scan complete | Evaluated: {len(_cycle_seen)} | "
            f"Signals fired: {self.signals_fired} | "
            f"Blocked age: {self.signals_blocked_age} | "
            f"Blocked score: {self.signals_blocked_score} | "
            f"Blocked security: {self.signals_blocked_security}"
        )

    async def _fetch_dexscreener(self) -> list:
        """Fetch pairs from DexScreener.

        Discovery strategy (per cycle ~22 API calls):
          1. 4 stub endpoints (boosts, profiles, takeovers) — enriched via /tokens batch
          2. 20 rotating keyword searches with chainId filter — direct pair data
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
            import time as _time
            _now_ms = _time.time() * 1000
            _max_age_ms = self.hard_skip_age_hours * 3600 * 1000
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
                    # Drop tokens older than hard_skip_age before expensive evaluation
                    created_ms = p.get("pairCreatedAt") or 0
                    if created_ms > 0 and (_now_ms - created_ms) > _max_age_ms:
                        continue
                    pairs_out.append(p)
            return pairs_out

        # Determine keywords for this cycle
        if self.scanner_keywords:
            # User-configured keyword list overrides the rotating pool
            cycle_keywords = self.scanner_keywords
            n_kw = len(cycle_keywords)
        else:
            # Pick 20 keywords from the rotating 80-keyword pool
            kw_pool = self._DEXSCREENER_KEYWORDS
            n_kw = 20
            offset = MultiSourceScanner._dex_keyword_offset % len(kw_pool)
            cycle_keywords = (kw_pool[offset:] + kw_pool[:offset])[:n_kw]
            MultiSourceScanner._dex_keyword_offset += n_kw

        try:
            async with aiohttp.ClientSession() as session:
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

                all_results = await asyncio.gather(
                    *stub_coros, *search_coros,
                    return_exceptions=True
                )
                stub_results = all_results[:4]
                search_results = all_results[4:4 + n_kw]

                # Collect stub addresses for enrichment
                stub_addresses: list = []
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

                # Collect search pairs (already full pair data)
                # Pre-filter by age here so we don't waste time on old tokens
                import time as _time
                _now_ms = _time.time() * 1000
                _max_age_ms = self.hard_skip_age_hours * 3600 * 1000
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
                        # Drop tokens older than hard_skip_age before enrichment
                        created_ms = p.get("pairCreatedAt") or 0
                        if created_ms > 0 and (_now_ms - created_ms) > _max_age_ms:
                            continue
                        direct_pairs.append(p)

                # Deduplicate all sources
                seen: dict = {}
                for p in enriched_pairs + direct_pairs:
                    addr_raw = p.get("baseToken", {}).get("address", "")
                    addr = addr_raw.lower()
                    if addr and addr not in seen:
                        seen[addr] = p
                        if addr_raw and addr_raw != addr and self.chain.chain_id == "solana":
                            self._mint_map[addr] = addr_raw

                logger.info(
                    f"[{self.chain.name}] DexScreener breakdown: "
                    f"stubs={len(enriched_pairs)} search={len(direct_pairs)} "
                    f"→ {len(seen)} unique"
                )
                return list(seen.values())

        except Exception as e:
            logger.error(f"[{self.chain.name}] DexScreener error: {e}")
            return []

    async def _fetch_dexscreener_trending(self) -> list:
        """io.dexscreener.com is IP-blocked by Cloudflare on Railway. Always returns []."""
        return []

    async def _fetch_pumpfun_graduated(self) -> list:
        """Fetch recently graduated pump.fun tokens via Solana RPC.

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
                mints: list = []
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
                enriched: list = []
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
            seen: dict = {}
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
                        return self._solanatracker_cache
                    if resp.status != 200:
                        logger.debug(f"[{self.chain.name}] SolanaTracker HTTP {resp.status}")
                        return []
                    data = await resp.json()

            if not isinstance(data, list):
                return []

            dex_chain = self.chain.dexscreener_chain
            pairs_out: list = []

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
                            "h1": vol24h / 24,
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

    async def _fetch_jupiter(self) -> list:
        """Fetch new Raydium pools via Solana RPC (catches all new Solana launches).

        Watches the Raydium AMM v4 program for recently initialized pools — this
        covers pump.fun graduates AND any other new token launching on Raydium.
        """
        if self.chain.chain_id != "solana":
            return []

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

                # Step 3: extract base token mints (non-quote tokens)
                mints: list = []
                for item in (tx_results if isinstance(tx_results, list) else []):
                    tx = item.get("result") or {}
                    if not tx:
                        continue
                    seen_in_tx: set = set()
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
                enriched: list = []
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

            seen: dict = {}
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
                            token_address = parts[1]

                            mcap_usd = attrs.get("market_cap_usd")
                            fdv_usd = attrs.get("fdv_usd")
                            if mcap_usd is not None:
                                mcap = float(mcap_usd)
                            elif fdv_usd is not None:
                                mcap = float(fdv_usd)
                            else:
                                mcap = 0.0

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
        """Convert a GeckoTerminal pool record into the dex_pair dict format
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
                "h1": volume_h24 / 24 if volume_h24 else 0,
                "h6": volume_h24 / 4 if volume_h24 else 0,
            },
            "priceChange": {
                "h1": price_change_h1,
                "h6": price_change_h24 / 4 if price_change_h24 else 0,
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
                    break
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

            liquid = [p for p in all_pairs if (p.get("liquidity") or 0) >= 10_000]
            liquid.sort(key=lambda p: p.get("volume24h") or 0, reverse=True)
            top200 = liquid[:200]

            if not top200:
                return []

            base_mints = [p.get("baseMint", "") for p in top200 if p.get("baseMint")]
            base_mints = list(dict.fromkeys(base_mints))

            self._mint_map.update({m.lower(): m for m in base_mints})

            enriched: list = []
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

            seen: dict = {}
            for p in enriched:
                addr = p.get("baseToken", {}).get("address", "").lower()
                if addr and addr not in seen:
                    seen[addr] = p

            return list(seen.values())

        except Exception as e:
            logger.warning(f"[{self.chain.name}] Raydium error: {e}")
            return []

    async def _resolve_mint_address(self, lowercase_addr: str) -> Optional[str]:
        """Look up the correct-case Solana mint address from Jupiter's token list."""
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
        """Build a signal from DexScreener pair data.

        birdeye_data: kept for interface compatibility — always pass {}
        from_pumpfun=True bypasses the pair-age and sells_h1 filters.
        """
        base = dex_pair.get("baseToken", {})
        token_address = base.get("address", "")
        token_symbol = base.get("symbol", "?")
        token_name = base.get("name", "Unknown")

        mcap = dex_pair.get("marketCap", 0)
        if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
            return None

        liquidity_for_estimate = dex_pair.get("liquidity", {}).get("usd", 0)
        if mcap == 0 and liquidity_for_estimate > 0:
            mcap = liquidity_for_estimate * 6
            if mcap < self.min_mcap:
                return None

        volume_h1 = dex_pair.get("volume", {}).get("h1", 0)
        volume_h6 = dex_pair.get("volume", {}).get("h6", 0)
        price_change_h1 = dex_pair.get("priceChange", {}).get("h1", 0) or 0
        price_change_h6 = dex_pair.get("priceChange", {}).get("h6", 0) or 0
        liquidity = dex_pair.get("liquidity", {}).get("usd", 0)
        txns_h1 = dex_pair.get("txns", {}).get("h1", {})
        buys_h1 = txns_h1.get("buys", 0)
        sells_h1 = txns_h1.get("sells", 0)

        # Rug prevention: hard filters before scoring
        if not from_pumpfun:
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
                    self.signals_blocked_age += 1
                    return None

            if buys_h1 > 0 and sells_h1 < 3:
                logger.info(
                    f"[{self.chain.name}] Rug filter (no sellers): {token_symbol} "
                    f"buys={buys_h1} sells={sells_h1} — no organic sell-side"
                )
                return None

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

        txns_available = not dex_pair.get("_gecko_source", False)
        dex_score = self._score_dexscreener(
            mcap, volume_h1, volume_h6, price_change_h1, price_change_h6,
            buys_h1, sells_h1, liquidity, has_social,
            txns_available=txns_available
        )

        # Birdeye removed — score starts as dex_score only
        birdeye_score = 0
        holder_count = 0
        holder_growth_pct = 0.0
        smart_money_buying = False
        combined = dex_score
        confirmed_by_both = False

        # Dip Sniper: 24h drop >= 25% AND 1h recovering >= 5%
        flags = []
        price_change_h24 = dex_pair.get("priceChange", {}).get("h24", 0) or 0
        if price_change_h24 <= -25 and price_change_h1 >= 5:
            combined += 15
            combined = min(combined, 100)
            flags.append("dip_setup")
            logger.info(
                f"[{self.chain.name}] DIP DETECTED: {token_symbol} | "
                f"24h: {price_change_h24:+.1f}% | 1h: {price_change_h1:+.1f}% | "
                f"Score +15 -> {combined}"
            )

        # Pump Chaser: 1h change >= 20% AND buy ratio >= 0.65 AND vol >= $20k
        total_txns = buys_h1 + sells_h1
        buy_ratio = buys_h1 / total_txns if total_txns > 0 else 0
        if price_change_h1 >= 20 and buy_ratio >= 0.65 and volume_h1 >= 20_000:
            combined += 10
            combined = min(combined, 100)
            flags.append("pump_setup")
            logger.info(
                f"[{self.chain.name}] PUMP DETECTED: {token_symbol} | "
                f"1h: {price_change_h1:+.1f}% | Buy ratio: {buy_ratio:.2f} | "
                f"Vol: ${volume_h1:,.0f} | Score +10 -> {combined}"
            )

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
            confirmed_by_both=confirmed_by_both,
            flags=flags,
            raw_pair_data=dex_pair
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

        # 6h trend
        if price_change_h6 > 50:
            score += 15
        elif price_change_h6 > 20:
            score += 10
        elif price_change_h6 > 5:
            score += 5
        elif price_change_h6 < -20:
            score -= 25
        elif price_change_h6 < 0:
            score -= 15

        # Buy pressure
        if not txns_available:
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
        if volume_h1 > 0 and volume_h6 > 0:
            vol_accel = volume_h1 / (volume_h6 / 6)
            if vol_accel >= 3.0:
                score += 10
            elif vol_accel >= 2.0:
                score += 6
            elif vol_accel >= 1.5:
                score += 3
            elif vol_accel < 0.5:
                score -= 8

        # Social
        if has_social:
            score += 10

        return max(0, min(100, score))

    def _score_birdeye(self, data: dict):
        """Kept for interface compatibility — Birdeye is removed.
        Returns (score, holder_count, holder_growth_pct, smart_money_buying).
        """
        return 0, 0, 0.0, False

    async def _evaluate_signal(self, signal: TokenSignal):
        """Evaluate a signal and decide whether to buy."""
        # Respect manual pause
        if self.tracker and self.tracker.buying_paused:
            return

        # Skip if already holding
        if signal.token_address.lower() in self.trader.open_positions:
            return

        # Skip well-known non-memecoin tokens
        _SKIP_MINTS = {
            "so11111111111111111111111111111111111111112",
            "epjfwdd5aufqssqem2qn1xzybapC8G4wEGGkZwyTDt1v".lower(),
            "es9vmfrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB".lower(),
        }
        if signal.token_address.lower() in _SKIP_MINTS:
            logger.debug(f"[{self.chain.name}] Skipping {signal.token_symbol} — base asset, not a memecoin")
            return

        # Block free-falling tokens
        if signal.price_change_h1 <= -15:
            logger.debug(
                f"[{self.chain.name}] Skipping {signal.token_symbol} — "
                f"free-falling: {signal.price_change_h1:.1f}% on 1h"
            )
            return

        # Score gate
        if signal.combined_score < self.min_combined_score:
            self.signals_blocked_score += 1

            # Dashboard watchlist: near-miss tokens scoring 45 to threshold
            if 45 <= signal.combined_score < self.min_combined_score:
                setup_tags = []
                if "dip_setup" in signal.flags:
                    setup_tags.append("Dip recovery")
                if "pump_setup" in signal.flags:
                    setup_tags.append("Pump momentum")
                reason = ", ".join(setup_tags) if setup_tags else (
                    f"Score {signal.combined_score} (DEX:{signal.dex_score})"
                )
                self._add_to_watchlist(signal, reason)

            logger.info(
                f"[{self.chain.name}] ❌ Low score: {signal.token_symbol} | "
                f"Score: {signal.combined_score} (need {self.min_combined_score}) | "
                f"DEX:{signal.dex_score} | "
                f"MCap: ${signal.mcap:,.0f} | "
                f"Vol1h: ${signal.volume_h1:,.0f} | "
                f"1h: {signal.price_change_h1:+.1f}%"
            )
            return

        # Score cap: over-pumped / late entry
        if signal.combined_score > self.max_combined_score:
            logger.info(
                f"[{self.chain.name}] Score-cap blocked: {signal.token_symbol} "
                f"score={signal.combined_score} > max {self.max_combined_score} "
                f"(over-pumped / rug risk)"
            )
            self.signals_blocked_score += 1
            return

        # Rug blacklist
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

        # Hard liquidity floor
        if signal.liquidity_usd < self.min_liquidity_usd:
            logger.info(
                f"[{self.chain.name}] Liquidity floor blocked: {signal.token_symbol} "
                f"${signal.liquidity_usd:,.0f} < ${self.min_liquidity_usd:,.0f}"
            )
            return

        # Volume floor
        if signal.volume_h1 < self.min_volume_h1_usd:
            logger.info(
                f"[{self.chain.name}] Volume floor blocked: {signal.token_symbol} "
                f"${signal.volume_h1:,.0f}/hr < ${self.min_volume_h1_usd:,.0f}/hr"
            )
            return

        # Sentiment check
        if self.sentiment_analyzer:
            sent = await self.sentiment_analyzer.analyze(
                signal.token_address,
                signal.token_symbol,
                signal.chain_id,
                signal.raw_pair_data
            )
            if not sent.passed:
                self.signals_blocked_score += 1
                logger.info(
                    f"[{self.chain.name}] 🚫 Sentiment blocked "
                    f"{signal.token_symbol} — {sent.sentiment_grade} "
                    f"({sent.sentiment_score}/100) | {', '.join(sent.flags) if sent.flags else 'below min score'}"
                )
                return

        # Rug classifier
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
                    is_mintable=False,
                    has_blacklist=False,
                    buy_tax=0.0,
                    sell_tax=0.0,
                    lp_locked=False,
                    top_holder_pct=0.0,
                    top5_holders_pct=0.0,
                    creator_prev_rugs=0,
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
                    f"{_rc_err} — skipping classifier, continuing"
                )

        # Security check
        sec_result = await self.security_checker.check(
            signal.token_address,
            self.chain.chain_id,
            signal.token_symbol
        )
        if not sec_result.passed:
            self.signals_blocked_security += 1
            logger.warning(
                f"[{self.chain.name}] 🛑 Security blocked "
                f"{signal.token_symbol} — {sec_result.risk_level}"
            )
            return

        # Stop-loss cooldown
        addr_lower = signal.token_address.lower()
        if addr_lower in self._sl_cooldown:
            remaining_min = int((self._sl_cooldown[addr_lower] - time.monotonic()) / 60)
            logger.debug(
                f"[{self.chain.name}] SL cooldown: {signal.token_symbol} "
                f"blocked for {remaining_min}m more"
            )
            return

        # Macro trend filter
        if signal.price_change_h6 < -20:
            logger.info(
                f"[{self.chain.name}] Macro trend blocked: {signal.token_symbol} "
                f"h6={signal.price_change_h6:+.1f}% — sustained downtrend, not a dip"
            )
            self.signals_blocked_score += 1
            return

        # Dip-buy chart check
        if not await self._chart_dip_check(signal, sec_result.risk_level):
            self.signals_blocked_score += 1
            return

        await self._fire_chart_buy(signal, sec_result.risk_level)

    async def process_external_signal(self,
                                     token_address: str,
                                     token_symbol: str,
                                     reason: str,
                                     signal_score: int = 70,
                                     strategy_tag: str = "external",
                                     skip_security: bool = False,
                                     price_usd: float = 0.0,
                                     liquidity_usd: float = 0.0,
                                     volume_h1: float = 0.0,
                                     ) -> bool:
        """
        Entry point for edge strategies (CrossWalletConvergence, CapitulationReversal)
        to route signals through the scanner's security checks before buying.

        Returns True if the buy was executed, False if blocked.
        """
        if signal_score < self.min_combined_score:
            self.signals_blocked_score += 1
            logger.info(
                f"[{self.chain.name}] [{strategy_tag}] ❌ Low score: "
                f"{token_symbol} | Score: {signal_score} "
                f"(need {self.min_combined_score})"
            )
            return False

        if token_address in self.trader.open_positions:
            logger.info(
                f"[{self.chain.name}] [{strategy_tag}] "
                f"Already holding {token_symbol} — skipping"
            )
            return False

        if self.trader.risk_manager.is_daily_limit_hit():
            return False

        if not skip_security:
            sec_result = await self.security_checker.check(
                token_address,
                self.chain.chain_id,
                token_symbol
            )
            if not sec_result.passed:
                self.signals_blocked_security += 1
                logger.warning(
                    f"[{self.chain.name}] [{strategy_tag}] 🛑 Security blocked "
                    f"{token_symbol} — {sec_result.risk_level}"
                )
                return False

        self.signals_fired += 1
        logger.info(
            f"[{self.chain.name}] [{strategy_tag}] 🎯 BUY SIGNAL: "
            f"{token_symbol} | Score: {signal_score} | {reason}"
        )

        await self.telegram.send(
            f"🎯 *{strategy_tag} Signal: ${token_symbol}*\n"
            f"🔗 Chain: {self.chain.name}\n"
            f"⭐ Score: {signal_score}\n"
            f"📝 {reason}"
        )

        await self.trader.buy(
            token_address=token_address,
            token_symbol=token_symbol,
            reason=f"[{self.chain.name}] {strategy_tag}: {reason}",
            signal_score=signal_score
        )
        return True

    async def _sol_macro_ok_check(self) -> bool:
        """Returns True if SOL's h1 price change is better than -3%."""
        now = time.monotonic()
        if now - self._sol_macro_ts < 300:
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
                logger.debug(f"[{self.chain.name}] SOL macro: h1={h1_change:+.1f}% — OK")
            return ok

        except Exception as e:
            logger.debug(f"[{self.chain.name}] SOL macro check error: {e}")
            return True

    async def _fetch_ohlcv(self, token_address: str) -> Optional[list]:
        """Fetch 30 × 5-minute OHLCV candles via GeckoTerminal (free, no key)."""
        if not token_address:
            return None
        candles = await self._fetch_ohlcv_gecko(token_address)
        return candles

    async def _fetch_ohlcv_gecko(self, token_address: str,
                                   aggregate: str = "5",
                                   limit: int = 30) -> Optional[list]:
        """Fetch OHLCV candles from GeckoTerminal (free, no key)."""
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

    def _analyze_chart(self, candles: list) -> dict:
        """Compute RSI(14), VWAP, and entry-timing signals from OHLCV candles."""
        closes  = [float(c[4]) for c in candles]
        highs   = [float(c[2]) for c in candles]
        lows    = [float(c[3]) for c in candles]
        volumes = [float(c[5]) for c in candles]

        # RSI(14)
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

        # VWAP
        vwap = None
        price_vs_vwap_pct = None
        typical = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        total_vol = sum(volumes)
        if total_vol > 0:
            vwap = sum(tp * v for tp, v in zip(typical, volumes)) / total_vol
            if closes:
                price_vs_vwap_pct = (closes[-1] - vwap) / vwap * 100

        # RSI pullback signal
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

        near_vwap = price_vs_vwap_pct is not None and price_vs_vwap_pct <= 10.0

        volume_declining = False
        if len(volumes) >= 15:
            recent_avg = sum(volumes[-5:]) / 5
            prior_avg  = sum(volumes[-15:-5]) / 10
            volume_declining = prior_avg > 0 and recent_avg < prior_avg * 0.80

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
            **self._entry_stage(closes, lows, volumes),
        }

    def _entry_stage(self, closes: list, lows: list, volumes: list) -> dict:
        """Detect whether we are early, mid, or late in a candle move."""
        if len(closes) < 4:
            return {"entry_stage": "unknown", "consecutive_green": 0,
                    "move_from_base_pct": 0.0, "vol_surge_ratio": 1.0}

        consecutive_green = 0
        for i in range(len(closes) - 2, 0, -1):
            if closes[i] > closes[i - 1]:
                consecutive_green += 1
            else:
                break

        base_low = min(lows[-min(10, len(lows)):])
        current  = closes[-1]
        move_from_base_pct = (
            (current - base_low) / base_low * 100
            if base_low > 0 else 0.0
        )

        prior_vols = volumes[-12:-2] if len(volumes) >= 12 else volumes[:-2]
        vol_10_avg = sum(prior_vols) / len(prior_vols) if prior_vols else (volumes[-2] if len(volumes) >= 2 else 1.0)
        last_complete_vol = volumes[-2] if len(volumes) >= 2 else volumes[-1]
        vol_surge_ratio = last_complete_vol / vol_10_avg if vol_10_avg > 0 else 1.0

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

    async def _chart_dip_check(self, signal: TokenSignal, risk_level: str = "UNKNOWN") -> bool:
        """Fetch 5m+1m candles and run all dip/recovery filters."""
        if not signal.token_address:
            return False

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
                reason = "no candles from GeckoTerminal — pool not indexed yet"
            else:
                reason = f"only {len(candles_5m)} candles — need ≥10 (token too new)"
            logger.info(
                f"[{self.chain.name}] Chart blocked: {signal.token_symbol} — {reason}"
            )
            return False

        candles = candles_5m

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

        # Hard block: RSI parabolic
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

        # Hard block: price severely extended above VWAP
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

        # Grinder filter
        candle_peak  = max(highs)
        candle_floor = min(lows)
        if candle_floor > 0 and candle_peak / candle_floor < 1.15:
            logger.info(
                f"[{self.chain.name}] Grinder filtered: {signal.token_symbol} "
                f"range {candle_peak / candle_floor:.2f}x — no meaningful price action"
            )
            return False

        # Dip range check
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

        # Rug-dump detection
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

        # Recovery signals
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
            f"DEX:{signal.dex_score} | "
            f"MCap: ${signal.mcap:,.0f}"
        )

        await self.telegram.send(
            f"*Scanner Signal: {signal.token_name} (${signal.token_symbol})*\n"
            f"Chain: {self.chain.name}\n\n"
            f"MCap: ${signal.mcap:,.0f}\n"
            f"1h: {signal.price_change_h1:+.1f}% | "
            f"6h: {signal.price_change_h6:+.1f}%\n"
            f"1h Vol: ${signal.volume_h1:,.0f}\n"
            f"Score: {signal.combined_score}/100 (DEX:{signal.dex_score})\n"
            f"Security: {risk_level}\n\n"
            f"[View on DexScreener]({signal.dex_url})"
        )

        await self.trader.buy(
            token_address=signal.token_address,
            token_symbol=signal.token_symbol,
            reason=(
                f"[{self.chain.name}] Multi-source score "
                f"{signal.combined_score} "
                f"(DEX:{signal.dex_score})"
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
            birdeye_score=0,
        )

    async def _watchlist_poll_cycle(self):
        """Re-check dip/recovery conditions for all dip watchlist tokens with a stored signal."""
        now = time.monotonic()
        to_remove = []

        for addr_lower, entry in list(self._dip_watchlist.items()):
            signal: TokenSignal = entry.get("signal")
            if not signal:
                continue

            if addr_lower in self._sl_cooldown:
                continue

            if addr_lower in self.trader.open_positions:
                to_remove.append(addr_lower)
                continue

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
        """Background task: poll dip watchlist tokens for dip entries every 60 seconds."""
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
        """Inject a token address directly into the evaluation pipeline."""
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
            signal = self._build_signal(pair, {})
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

    def get_watchlist_recommendations(self) -> list:
        """Return dip watchlist tokens with signals for the recommendations dashboard panel."""
        now = time.monotonic()
        result = []
        for addr_lower, entry in self._dip_watchlist.items():
            signal = entry.get("signal")
            if not signal:
                continue
            peak = entry.get("peak_price", 0)
            dip_pct = ((signal.price_usd - peak) / peak * 100) if peak > 0 else 0
            age_min = int((now - entry["added_at"]) / 60)
            result.append({
                "token_address": signal.token_address,
                "token_symbol":  signal.token_symbol,
                "token_name":    signal.token_name,
                "chain":         self.chain.name,
                "chain_id":      self.chain.chain_id,
                "mcap":          signal.mcap,
                "volume_h1":     signal.volume_h1,
                "score":         signal.combined_score,
                "dex_score":     signal.dex_score,
                "birdeye_score": 0,
                "price_usd":     signal.price_usd,
                "price_change_h1": signal.price_change_h1,
                "price_change_h6": signal.price_change_h6,
                "dip_pct":       round(dip_pct, 1),
                "dex_url":       signal.dex_url,
                "risk_level":    entry.get("risk_level", "UNKNOWN"),
                "watching_min":  age_min,
            })
        result.sort(key=lambda x: x["score"], reverse=True)
        return result

    # ── Dashboard watchlist management ──────────────────────────────────────

    def _add_to_watchlist(self, signal: TokenSignal, reason: str):
        """Add a near-miss token to the dashboard watchlist (cap at 20, drop lowest)."""
        entry = {
            "symbol": signal.token_symbol,
            "score": signal.combined_score,
            "mcap": signal.mcap,
            "price": signal.price_usd,
            "reason": reason,
            "timestamp": time.time(),
            "flags": signal.flags,
            "dex_url": signal.dex_url,
        }
        self.watchlist[signal.token_address] = entry

        if len(self.watchlist) > self._watchlist_max:
            worst_addr = min(
                self.watchlist, key=lambda a: self.watchlist[a]["score"]
            )
            del self.watchlist[worst_addr]

    def _prune_watchlist(self):
        """Remove dashboard watchlist entries older than 2 hours."""
        cutoff = time.time() - self._watchlist_ttl
        stale = [
            addr for addr, entry in self.watchlist.items()
            if entry["timestamp"] < cutoff
        ]
        for addr in stale:
            del self.watchlist[addr]

    def get_watchlist(self) -> list:
        """Return dashboard watchlist as a sorted list (highest score first)."""
        self._prune_watchlist()
        result = []
        for addr, entry in self.watchlist.items():
            item = dict(entry)
            item["token_address"] = addr
            item["age_seconds"] = int(time.time() - entry["timestamp"])
            result.append(item)
        result.sort(key=lambda x: x["score"], reverse=True)
        return result

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

    _SOL_PRICE_FALLBACK = 130.0
    _SOL_MINT = "so11111111111111111111111111111111111111112"

    def __init__(self, scanner: MultiSourceScanner, price_feed=None):
        self.scanner = scanner
        self.price_feed = price_feed

    def _get_sol_price(self) -> float:
        """Return live SOL/USD price from the price feed, falling back to 130."""
        if self.price_feed is not None:
            try:
                tick = self.price_feed.get_latest(self._SOL_MINT)
                if tick and tick.price_usd and tick.price_usd > 0:
                    return tick.price_usd
            except Exception:
                pass
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

        token_address = mint

        cache_key = f"solana:{token_address.lower()}"
        if cache_key in self.scanner.seen_tokens:
            return

        symbol = msg.get("symbol", "?")
        name = msg.get("name", symbol)
        market_cap_sol = float(msg.get("marketCapSol") or 0)
        v_sol = float(msg.get("vSol") or 0)
        initial_buy = float(msg.get("initialBuy") or 0)

        sol_price = self._get_sol_price()
        mcap_usd = market_cap_sol * sol_price

        if not (self.scanner.min_mcap <= mcap_usd <= self.scanner.max_mcap):
            return

        logger.info(f"[PumpFun] New token: {symbol} mcap=${mcap_usd:,.0f} (SOL=${sol_price:.0f})")

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
                "h1": 0.1,
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
