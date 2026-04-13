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
    mcap: float = 0.0
    price_usd: float = 0.0
    volume_h1: float = 0.0
    volume_h6: float = 0.0
    price_change_h1: float = 0.0
    price_change_h6: float = 0.0
    liquidity_usd: float = 0.0
    buy_count_h1: int = 0
    sell_count_h1: int = 0
    holder_count: int = 0
    holder_growth_pct: float = 0.0  # Holder growth in last hour
    smart_money_buying: bool = False  # Large wallets accumulating
    dex_score: int = 0              # 0-100 from DexScreener data
    birdeye_score: int = 0          # Kept for interface compatibility — always 0 (Birdeye removed)
    combined_score: int = 0         # Final combined score
    has_social: bool = False
    dex_url: str = ""
    confirmed_by_both: bool = False  # True only if both sources agree
    hh_hl_confirmed: bool = False  # From signal evaluator — HH+HL structure
    pair_address: str = ""          # DEX pool address (used for GeckoTerminal OHLCV)
    flags: List[str] = field(default_factory=list)
    raw_pair_data: dict = field(default_factory=dict)  # Original DexScreener pair data
    chart_score: int = 0
    chart_pattern: str = ""
    age_hours: float = 0.0  # Token pair age in hours at signal time


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
                 min_liquidity_usd: float = 10_000,
                 min_volume_h1_usd: float = 15_000,
                 max_volume_h1_usd: float = 150_000,
                 min_combined_score: int = 50,
                 max_combined_score: int = 85,
                 require_both_sources: bool = False,
                 min_holder_count: int = 100,
                 single_source_min_score: int = 70,
                 max_dev_wallet_pct: float = 5.0,
                 pyramid_score_threshold: int = 90,
                 hard_skip_age_hours: float = 999.0,
                 rug_classifier=None,
                 sentiment_analyzer=None,
                 tracker=None,
                 startup_delay: float = 0,
                 scanner_keywords: List[str] = None,
                 realtime_signal_layer=None,
                 chart_min_score: int = 10,
                 chart_chaos_range_pct: float = 30.0,
                 chart_dead_vol_ratio: float = 0.3):
        self.chain = chain
        self.trader = trader
        self.min_age_hours = 0.0
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
        self.max_volume_h1_usd = max_volume_h1_usd
        self.min_combined_score = min_combined_score
        self.max_combined_score = max_combined_score
        self.require_both_sources = require_both_sources
        self.min_holder_count = min_holder_count
        self.single_source_min_score = single_source_min_score
        self.tracker = tracker
        self.startup_delay = startup_delay

        # scanner_keywords: if provided, overrides the rotating pool for keyword search
        self.scanner_keywords = scanner_keywords  # None = use _DEXSCREENER_KEYWORDS rotation

        # Real-time signal layer (TickPatternDetector + OrderBookScorer)
        self.realtime_signal_layer = realtime_signal_layer

        # Chart quality gates
        self.chart_min_score = chart_min_score
        self.chart_chaos_range_pct = chart_chaos_range_pct
        self.chart_dead_vol_ratio = chart_dead_vol_ratio

        # LRU-bounded seen_tokens — evict oldest when >500 entries
        self.seen_tokens: set = set()
        self._seen_tokens_order: list = []

        self.evaluator = TokenSignalEvaluator(
            min_liquidity_usd=min_liquidity_usd,
            max_dev_wallet_pct=max_dev_wallet_pct,
            hard_skip_age_hours=hard_skip_age_hours,
            pyramid_score_threshold=pyramid_score_threshold
        )
        self.signals_fired: int = 0
        self.signals_blocked_security: int = 0
        self.signals_blocked_score: int = 0
        self.signals_blocked_age: int = 0
        self.signals_blocked_mcap: int = 0
        self.signals_blocked_rug: int = 0
        self._last_buy_time: float = 0.0        # monotonic time of most recent buy signal fired
        self._start_monotonic: float = time.monotonic()  # for watchdog uptime calc
        # Tokens flagged as PUMP DETECTED (h1>15%) — blocked for 30 min to prevent
        # buying them again after DexScreener h1 window rolls and returns 0%.
        self._pump_cooldown: dict = {}  # addr_lower -> monotonic timestamp of detection

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

        # In-flight buy guard: tokens currently being bought across any scanner path.
        # Prevents race-condition duplicate buys when Axiom + EstablishedScanner fire simultaneously.
        # Format: set of addr_lower strings
        self._pending_buys: set = set()

        # Chaos block history: tokens that failed the chaos check recently.
        # Prevents fallback path from bypassing the chaos gate.
        # Format: addr_lower → expiry_monotonic_time (30 min)
        self._chaos_blocked: Dict[str, float] = {}

        # Volume deceleration cooldown: tokens blocked for dying volume can't
        # re-enter evaluation for 10 min — prevents re-buying into a stale pump
        # when a brief volume tick makes the ratio look passable again.
        # Format: addr_lower → expiry_monotonic_time (10 min)
        self._vol_decel_blocked: Dict[str, float] = {}

        # Stop-loss cooldown: after a stop-loss fires, block that token for 1h.
        # Format: addr_lower → expiry_monotonic_time
        self._sl_cooldown: Dict[str, float] = {}
        self._sl_cooldown_path = os.path.join(
            os.environ.get("DATA_DIR", "."), "sl_cooldowns.json"
        )
        self._load_sl_cooldowns()

        # No-candle block: after 3 failed GeckoTerminal attempts, block re-adding
        # to watchlist for 1 hour so the main scanner doesn't reset the counter.
        # Format: addr_lower → expiry_monotonic_time
        self._no_candle_block: Dict[str, float] = {}

        # SOL macro cache: cache the SOL h1 price change for 5 minutes
        self._sol_macro_ts: float = 0
        self._sol_macro_ok: bool = True

        # Dashboard watchlist — near-miss tokens scoring 45-64 (DIFFERENT from _dip_watchlist)
        self.watchlist: Dict[str, dict] = {}
        self._watchlist_max = 20
        self._watchlist_ttl = 7200  # 2 hours in seconds

        # GeckoTerminal pool address cache: token_address.lower() → pool_address
        # Pool addresses are permanent on-chain. Bounded to 512 entries.
        # One MultiSourceScanner instance per chain — no namespace needed.
        self._gecko_pool_cache: Dict[str, str] = {}
        self._gecko_pool_cache_max: int = 512

        # Axiom real-time price feed — set externally after construction.
        # When set, overrides stale DexScreener price/volume/liquidity with live data.
        self.axiom_price_feed = None

    def set_solanatracker_key(self, api_key: str):
        """Set the SolanaTracker API key for enhanced pump.fun discovery."""
        self.solanatracker_api_key = api_key

    def _apply_axiom_overrides(self, dex_tokens: list):
        """
        Override stale DexScreener price/volume/liquidity fields with live
        Axiom data for any token that AxiomPriceFeed has in its caches.
        Only runs when self.axiom_price_feed is set.
        """
        if self.axiom_price_feed is None:
            return
        feed = self.axiom_price_feed
        override_count = 0
        for t in dex_tokens:
            token_address = t.get("baseToken", {}).get("address", "").lower()
            if not token_address:
                continue
            if token_address not in feed.price_cache:
                continue
            t["priceUsd"] = feed.price_cache[token_address]
            if token_address in feed.volume_cache:
                vol = t.get("volume")
                if not isinstance(vol, dict):
                    t["volume"] = {}
                t["volume"]["h1"] = feed.volume_cache[token_address]
            if token_address in feed.liquidity_cache:
                liq = t.get("liquidity")
                if not isinstance(liq, dict):
                    t["liquidity"] = {}
                t["liquidity"]["usd"] = feed.liquidity_cache[token_address]
            if token_address in feed.change_cache:
                chg = t.get("priceChange")
                if not isinstance(chg, dict):
                    t["priceChange"] = {}
                t["priceChange"]["h1"] = feed.change_cache[token_address]
            override_count += 1
        if override_count:
            logger.debug(
                f"[{self.chain.name}] Axiom price overrides applied: {override_count} token(s)"
            )

    def register_stop_loss(self, token_address: str, token_symbol: str, exit_price: float,
                           cooldown_seconds: int = 3600):
        """
        Called by the position manager when any losing exit fires.
        Blocks re-entry on this token for cooldown_seconds (default 1h).
        After cooldown expires the token must pass a full fresh scan — it is NOT auto-bought.
        """
        addr_lower = token_address.lower()
        cooldown_until = time.monotonic() + cooldown_seconds
        self._sl_cooldown[addr_lower] = cooldown_until
        # Also evict from dip watchlist so it can't auto-buy from a stale watchlist entry
        if addr_lower in self._dip_watchlist:
            del self._dip_watchlist[addr_lower]
            logger.info(
                f"[{self.chain.name}] Loss cooldown: {token_symbol} removed from dip watchlist"
            )
        label = f"{cooldown_seconds // 60}min" if cooldown_seconds < 3600 else f"{cooldown_seconds // 3600}h"
        logger.info(
            f"[{self.chain.name}] Loss cooldown: {token_symbol} blocked for {label} — "
            f"must pass fresh scan to re-enter (addr={addr_lower[:8]}…)"
        )
        self._save_sl_cooldowns()

    def _save_sl_cooldowns(self):
        """Persist active cooldowns to disk so they survive Railway restarts."""
        now_wall = time.time()
        now_mono = time.monotonic()
        # Store wall-clock expiry (time.time()) — monotonic can't survive restarts
        data = {
            addr: now_wall + (expiry_mono - now_mono)
            for addr, expiry_mono in self._sl_cooldown.items()
            if expiry_mono > now_mono
        }
        try:
            with open(self._sl_cooldown_path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"[{self.chain.name}] Could not save sl_cooldowns: {e}")

    def _load_sl_cooldowns(self):
        """Load persisted cooldowns on startup and convert back to monotonic time."""
        try:
            with open(self._sl_cooldown_path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        now_wall = time.time()
        now_mono = time.monotonic()
        loaded = 0
        for addr, expiry_wall in data.items():
            remaining = expiry_wall - now_wall
            if remaining > 0:
                self._sl_cooldown[addr] = now_mono + remaining
                loaded += 1
        if loaded:
            logger.info(f"Restored {loaded} sl_cooldown(s) from disk")

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
                   if now - e.get("added_at", now) > _WATCHLIST_TTL]
        for addr in expired:
            del self._dip_watchlist[addr]

        # Expire elapsed stop-loss cooldowns
        self._sl_cooldown = {a: t for a, t in self._sl_cooldown.items() if t > now}

        # Expire elapsed no-candle blocks
        self._no_candle_block = {a: t for a, t in self._no_candle_block.items() if t > now}
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

        # Override stale DexScreener prices with live Axiom data where available
        self._apply_axiom_overrides(dex_tokens)

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
                logger.warning(f"[{self.chain.name}] Token eval error: {e}", exc_info=True)

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
                logger.warning(f"[{self.chain.name}] GeckoTerminal token eval error: {e}", exc_info=True)

        # Send scan summary AFTER evaluation so counts are accurate
        logger.info(
            f"[{self.chain.name}] SUMMARY eval={len(_cycle_seen)} "
            f"fired={self.signals_fired} age={self.signals_blocked_age} "
            f"mcap={self.signals_blocked_mcap} rug={self.signals_blocked_rug} "
            f"score={self.signals_blocked_score} sec={self.signals_blocked_security}"
        )
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
                # Track boosted addresses (from token-boosts endpoints only)
                _boost_addrs: set = set()
                stub_addresses: list = []
                for idx, raw in enumerate(stub_results):
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
                            # stub_results[0] = top boosts, stub_results[1] = latest boosts
                            if idx < 2:
                                _boost_addrs.add(addr.lower())

                stub_addresses = list(dict.fromkeys(stub_addresses))
                enriched_pairs = await _enrich_addresses(session, stub_addresses)

                # Mark boosted pairs
                for p in enriched_pairs:
                    addr_raw = p.get("baseToken", {}).get("address", "")
                    if addr_raw.lower() in _boost_addrs:
                        p["_boosted"] = True

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

        # pump.fun → Raydium migration program (dedicated graduation program — every tx = one grad)
        # PumpSwap graduates are caught via Axiom WS + DexScreener polling; the PumpSwap AMM
        # program handles all swaps across all pools and can't be cleanly filtered for grads only.
        PUMP_MIGRATION = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
        SOL_MINT = "So11111111111111111111111111111111111111112"
        rpc_url = self.trader.rpc_url

        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: get recent graduation signatures
                async with session.post(rpc_url, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [PUMP_MIGRATION, {"limit": 15, "commitment": "finalized"}]
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
                                # Use $50k floor to catch PumpSwap graduates (~$67k at graduation)
                                # which land below the standard min_mcap of $70k.
                                grad_min = min(self.min_mcap, 50_000)
                                if mcap != 0 and not (grad_min <= mcap <= self.max_mcap):
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
                    "params": [RAYDIUM_AMM, {"limit": 15, "commitment": "finalized"}]
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

        # Spam blacklist — dead tokens that flood DexScreener search results
        _SPAM_SYMBOLS = {"VANGUARD", "AI", "RSCOIN", "RSCOIN"}
        if token_symbol.upper() in _SPAM_SYMBOLS:
            return None

        mcap = dex_pair.get("marketCap", 0)
        if mcap != 0 and not (self.min_mcap <= mcap <= self.max_mcap):
            self.signals_blocked_mcap += 1
            return None

        liquidity_for_estimate = dex_pair.get("liquidity", {}).get("usd", 0)
        if mcap == 0 and liquidity_for_estimate > 0:
            mcap = liquidity_for_estimate * 6
            if mcap < self.min_mcap:
                self.signals_blocked_mcap += 1
                return None

        volume_h1 = dex_pair.get("volume", {}).get("h1", 0)
        volume_h6 = dex_pair.get("volume", {}).get("h6", 0)
        price_change_h1 = dex_pair.get("priceChange", {}).get("h1", 0) or 0
        price_change_h6 = dex_pair.get("priceChange", {}).get("h6", 0) or 0
        liquidity = dex_pair.get("liquidity", {}).get("usd", 0)
        txns_h1 = dex_pair.get("txns", {}).get("h1", {})
        buys_h1 = txns_h1.get("buys", 0)
        sells_h1 = txns_h1.get("sells", 0)

        # SolanaTracker rug flag: skip tokens flagged as rugged or very high risk
        st_risk = dex_pair.get("_st_risk") or {}
        if st_risk:
            if st_risk.get("rugged") is True or float(st_risk.get("score") or 0) >= 0.8:
                logger.info(
                    f"[{self.chain.name}] Rug flag (SolanaTracker): {token_symbol} — "
                    f"rugged={st_risk.get('rugged')} score={st_risk.get('score')}"
                )
                self.signals_blocked_rug += 1
                return None

        # Rug prevention: hard filters before scoring
        pair_age_hours = 0.0
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
                    self.signals_blocked_rug += 1
                    return None
                if pair_age_hours > self.hard_skip_age_hours:
                    self.signals_blocked_age += 1
                    return None

            if buys_h1 > 0 and sells_h1 < 3:
                logger.info(
                    f"[{self.chain.name}] Rug filter (no sellers): {token_symbol} "
                    f"buys={buys_h1} sells={sells_h1} — no organic sell-side"
                )
                self.signals_blocked_rug += 1
                return None

            total_txns = buys_h1 + sells_h1
            if total_txns >= 20 and sells_h1 / total_txns < 0.15:
                logger.info(
                    f"[{self.chain.name}] Rug filter (low sell ratio): {token_symbol} "
                    f"sells={sells_h1}/{total_txns} ({sells_h1/total_txns:.1%}) < 15%"
                )
                self.signals_blocked_rug += 1
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

        # Boosted token bonus: +5 for tokens from DexScreener token-boost endpoints
        flags = []
        if dex_pair.get("_boosted"):
            combined += 5
            combined = min(combined, 100)
            flags.append("boosted")
            logger.info(
                f"[{self.chain.name}] BOOSTED: {token_symbol} | Score +5 -> {combined}"
            )

        # Dip Sniper: 24h drop >= 25% AND 1h recovering >= 5%
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

        # Pump Chaser: 1h change >= 20% and <= 75% AND vol >= $20k
        # Cap at 75% — tokens already up >75% in 1h are usually topping out.
        # For DexScreener sources (txns_available=True): also require buy_ratio >= 0.65
        # For GeckoTerminal sources (txns_available=False): buy/sell counts are unavailable,
        # so rely on price momentum + volume alone — buy_ratio check skipped
        total_txns = buys_h1 + sells_h1
        buy_ratio = buys_h1 / total_txns if total_txns > 0 else 0
        _pump_ratio_ok = (not txns_available) or (buy_ratio >= 0.65)
        if 15 <= price_change_h1 <= 35 and _pump_ratio_ok and volume_h1 >= 20_000:
            combined += 10
            combined = min(combined, 100)
            flags.append("pump_setup")
            logger.info(
                f"[{self.chain.name}] PUMP DETECTED: {token_symbol} | "
                f"1h: {price_change_h1:+.1f}% | Buy ratio: {'N/A (gecko)' if not txns_available else f'{buy_ratio:.2f}'} | "
                f"Vol: ${volume_h1:,.0f} | Score +10 -> {combined}"
            )
            # Record cooldown — block re-entry for 30 min even if h1 rolls to 0%
            self._pump_cooldown[token_address.lower()] = time.monotonic()

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
            raw_pair_data=dex_pair,
            age_hours=round(pair_age_hours, 2),
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

        # 1h price momentum — reward pre-move accumulation, penalize pumped tokens
        if price_change_h1 > 30:
            score -= 15   # Parabolic = exit liquidity — penalize hard
        elif price_change_h1 > 15:
            score += 0    # Late — skip the bonus
        elif price_change_h1 > 5:
            score += 7    # Reasonable moderate move, still early
        elif price_change_h1 >= 0:
            # Pre-move accumulation — only reward if volume confirms it
            if volume_h1 > 30_000:
                score += 14   # Volume building without price yet = ideal entry
        elif price_change_h1 < -15:
            score -= 15   # Downtrend

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
        if self.tracker and getattr(self.tracker, 'buying_paused', False):
            return

        # Skip if already holding or buy already in-flight (race-condition guard)
        addr_lower = signal.token_address.lower()
        if addr_lower in self.trader.open_positions or addr_lower in self._pending_buys:
            return

        # SOL macro gate — don't buy when SOL 1h is < -3% (meme market cooling)
        if not await self._sol_macro_ok_check():
            return

        # Re-entry filter removed — score already incorporates price trend.
        # A score ≥70 on a previously-held token means the scorer judged it worth buying.

        # Skip well-known non-memecoin tokens
        _SKIP_MINTS = {
            "so11111111111111111111111111111111111111112",
            "epjfwdd5aufqssqem2qn1xzybapC8G4wEGGkZwyTDt1v".lower(),
            "es9vmfrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB".lower(),
        }
        if signal.token_address.lower() in _SKIP_MINTS:
            logger.debug(f"[{self.chain.name}] Skipping {signal.token_symbol} — base asset, not a memecoin")
            return

        # Score gate
        if signal.combined_score < self.min_combined_score:
            self.signals_blocked_score += 1

            # Dashboard watchlist: near-miss tokens scoring 30 to threshold
            if 30 <= signal.combined_score < self.min_combined_score:
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

        # Trading hours gate DISABLED — trading 24/7

        # Score cap: over-pumped / late entry — pump chaser DISABLED (7% WR, -$109 loss)
        # Dip recoveries are exempt: a high score on a dip_setup means strong fundamentals
        # + recovery signal — NOT an over-pumped entry.
        if signal.combined_score > self.max_combined_score and "dip_setup" not in signal.flags:
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

        # Volume floor — dip_setup tokens are exempt: a 24h crash naturally suppresses
        # 1h volume; recovery volume builds after the bounce begins.
        if signal.volume_h1 < self.min_volume_h1_usd and "dip_setup" not in signal.flags:
            logger.info(
                f"[{self.chain.name}] Volume floor blocked: {signal.token_symbol} "
                f"${signal.volume_h1:,.0f}/hr < ${self.min_volume_h1_usd:,.0f}/hr"
            )
            return

        # Volume ceiling disabled — letting high-volume tokens through

        # Volume/MCap ratio — <1% = dead token (60-80% of volume is bots; ratio reveals organic activity)
        if signal.mcap > 0:
            vol_mcap_ratio = signal.volume_h1 / signal.mcap
            if vol_mcap_ratio < 0.01:
                logger.info(
                    f"[{self.chain.name}] Dead volume blocked: {signal.token_symbol} "
                    f"vol/mcap={vol_mcap_ratio*100:.2f}% < 1% "
                    f"(vol=${signal.volume_h1:,.0f} mcap=${signal.mcap:,.0f})"
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

        # Security check — detect pool type to set LP lock requirements correctly.
        # bonding_curve=True (pump-fun pre-grad): no LP to lock, skip LP requirement.
        # pumpswap=True: LP burned by protocol on graduation, rugcheck may lag — skip LP check.
        # All other graduated pools (raydium, meteora, etc.) must have LP locked.
        _is_micro = signal.mcap > 0 and signal.mcap <= 80_000
        _dex_id = (signal.raw_pair_data or {}).get("dexId", "").lower()
        # DexScreener reports dexId="pump-fun" for pre-graduation bonding curves.
        # GeckoTerminal/RPC sources have raw_pair_data={} so dexId is always "".
        # For those sources, fall back: Solana micro-caps with no explicit graduated-pool
        # dexId are treated as bonding curves to avoid false LP-lock blocks.
        _is_bonding_curve = (_dex_id == "pump-fun") or (
            self.chain.chain_id == "solana" and _is_micro and _dex_id == ""
        )
        _is_pumpswap = (_dex_id == "pumpswap")
        sec_result = await self.security_checker.check(
            signal.token_address,
            self.chain.chain_id,
            signal.token_symbol,
            micro_cap=_is_micro,
            bonding_curve=_is_bonding_curve,
            pumpswap=_is_pumpswap,
        )
        if not sec_result.passed:
            self.signals_blocked_security += 1
            logger.warning(
                f"[{self.chain.name}] 🛑 Security blocked "
                f"{signal.token_symbol} — {sec_result.risk_level}"
            )
            return

        # Stop-loss cooldown
        if addr_lower in self._sl_cooldown:
            remaining_min = int((self._sl_cooldown[addr_lower] - time.monotonic()) / 60)
            logger.debug(
                f"[{self.chain.name}] SL cooldown: {signal.token_symbol} "
                f"blocked for {remaining_min}m more"
            )
            return

        # H1 and H6 must both be green — if either is red the trend is not with us.
        # Exempt dip_setup: those are intentional recovery plays off a 24h drop.
        if "dip_setup" not in signal.flags:
            if signal.price_change_h1 <= 0:
                logger.info(
                    f"[{self.chain.name}] Red h1 blocked: {signal.token_symbol} "
                    f"h1={signal.price_change_h1:+.1f}% — must be green before entry"
                )
                self.signals_blocked_score += 1
                return
            if signal.price_change_h6 <= 0:
                logger.info(
                    f"[{self.chain.name}] Red h6 blocked: {signal.token_symbol} "
                    f"h6={signal.price_change_h6:+.1f}% — must be green before entry"
                )
                self.signals_blocked_score += 1
                return

        # Late-entry guard: if the token has already pumped hard in the last hour, skip it.
        # High h1 scores well but by the time the signal fires, early buyers are already exiting.
        # The DipWatcher can re-catch these after they pull back.
        if signal.price_change_h1 > 35 and "dip_setup" not in signal.flags:
            logger.info(
                f"[{self.chain.name}] Late-entry blocked: {signal.token_symbol} "
                f"h1={signal.price_change_h1:+.1f}% — pump already happened, skip"
            )
            self.signals_blocked_score += 1
            return

        # Macro trend filter — exempt dip_setup: a 24h crash will always show negative h6;
        # _chart_dip_check downstream will validate the actual recovery candles.
        if signal.price_change_h6 < -20 and "dip_setup" not in signal.flags:
            logger.info(
                f"[{self.chain.name}] Macro trend blocked: {signal.token_symbol} "
                f"h6={signal.price_change_h6:+.1f}% — sustained downtrend, not a dip"
            )
            self.signals_blocked_score += 1
            return

        # Dump-in-progress guard: m5 too negative means active selling, not a dip
        # Healthy dips are -2% to -10%; below -12% is a crash or dev dump
        _pc_m5 = float((signal.raw_pair_data or {}).get("priceChange", {}).get("m5", 0) or 0)

        if _pc_m5 < -12:
            logger.info(
                f"[{self.chain.name}] Dump guard blocked: {signal.token_symbol} "
                f"m5={_pc_m5:+.1f}% — crash in progress, not a dip"
            )
            self.signals_blocked_score += 1
            return

        # Momentum entry guard: require m5 to be positive or near-flat at entry.
        # Entering into active decline (m5 < -3%) means buying a reversal, not a dip.
        # Data shows winners move within minutes of entry — losers are already falling when we buy.
        # Exempt dip_setup (recovery plays where positive m5 after a crash is the signal).
        if "dip_setup" not in signal.flags:
            if _pc_m5 < -3:
                logger.info(
                    f"[{self.chain.name}] Declining m5 blocked: {signal.token_symbol} "
                    f"m5={_pc_m5:+.1f}% — price actively falling, not entering into decline"
                )
                self.signals_blocked_score += 1
                return

        # Trap-pump guard: h1 looks positive only because of an earlier pump inside
        # a multi-hour downtrend. Cross-check h6 vs h1 — if h6 is significantly
        # negative but h1 is only modest, the h1 signal is deceptive.
        # Exempt dip_setup: the 24h drop is intentional; _chart_dip_check validates the recovery.
        if signal.price_change_h6 < -10 and signal.price_change_h1 < 20 and "dip_setup" not in signal.flags:
            logger.info(
                f"[{self.chain.name}] Trap-pump blocked: {signal.token_symbol} "
                f"h6={signal.price_change_h6:+.1f}% h1={signal.price_change_h1:+.1f}% "
                f"— brief pump inside multi-hour downtrend"
            )
            self.signals_blocked_score += 1
            return

        # Dip-buy chart check
        if not await self._chart_dip_check(signal, sec_result.risk_level):
            self.signals_blocked_score += 1
            return

        # Real-time signal boost (tick patterns + order book scoring)
        if self.realtime_signal_layer is not None:
            try:
                rt_score = self.realtime_signal_layer.score(signal.token_address, signal.price_usd)
                if rt_score > 0:
                    signal.combined_score = min(100, signal.combined_score + rt_score)
                    logger.info(
                        f"[{self.chain.name}] RT signal +{rt_score}: "
                        f"{signal.token_symbol} -> score now {signal.combined_score}"
                    )
            except Exception as _rt_err:
                logger.debug(f"[{self.chain.name}] RT signal error: {_rt_err}")

        # 90-second bounce confirmation — non-blocking
        # If price fades more than 1.5% in 90s after dip check passes, skip the buy.
        # Catches "entering into continued dumps" that stop out within 2 minutes.
        if signal.price_usd <= 0:
            logger.info(
                f"[{self.chain.name}] ⏭ Skipping bounce confirmation for {signal.token_symbol} "
                f"— no price data (price_usd=0), cannot verify fade"
            )
            return
        asyncio.create_task(
            self._confirm_and_buy(signal, sec_result.risk_level, signal.price_usd)
        )

    async def _confirm_and_buy(
        self, signal: "TokenSignal", risk_level: str, price_at_check: float
    ):
        """
        Wait 45 seconds after the dip check passes, then verify the bounce is still holding.
        If price has dropped >1.5% from the dip-check snapshot, abort — the bounce faded.
        Reduced from 90s: chart dip check already validates 7 recovery signals; 150s total
        delay was causing missed entries in fast-moving memecoins.
        """
        sym = signal.token_symbol
        addr = signal.token_address
        logger.info(
            f"[{self.chain.name}] ⏳ Confirming bounce: {sym} — "
            f"waiting 20s to verify recovery holds (price=${price_at_check:.6f})"
        )
        await asyncio.sleep(20)

        # Skip if we're already in this position (another path bought it)
        if addr.lower() in self.trader.open_positions:
            logger.info(
                f"[{self.chain.name}] ⏭ {sym} already held — skipping delayed buy"
            )
            return

        # Re-fetch current price — try DexScreener first, fall back to price feed caches
        current_price: float = 0.0
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs") or []
                        chain_pairs = [
                            p for p in pairs
                            if p.get("chainId", "").lower() == self.chain.chain_id
                        ]
                        if chain_pairs:
                            _grad = [p for p in chain_pairs if p.get("dexId", "") != "pump-fun" and p.get("liquidity", {}).get("usd", 0) > 1000]
                            pair = max(
                                _grad or chain_pairs,
                                key=lambda p: p.get("liquidity", {}).get("usd", 0),
                            )
                            current_price = float(
                                pair.get("priceUsd") or pair.get("price", 0) or 0
                            )
        except Exception as _e:
            logger.debug(f"[{self.chain.name}] Confirm-buy price fetch error: {_e}")

        # Fall back to live price feed caches (DexScreener poller, Axiom feed)
        if current_price <= 0:
            _dex = getattr(self.trader, "_dex_price_feed", None)
            if _dex is not None:
                current_price = (
                    getattr(_dex, "price_cache", {}).get(addr.lower(), 0.0)
                    or getattr(_dex, "price_cache", {}).get(addr, 0.0)
                )
        if current_price <= 0:
            _axiom = getattr(self.trader, "_axiom_price_feed", None)
            if _axiom is not None:
                current_price = getattr(_axiom, "price_cache", {}).get(addr, 0.0)

        if current_price > 0 and price_at_check > 0:
            change_pct = (current_price - price_at_check) / price_at_check * 100
            if change_pct < -1.0:
                logger.info(
                    f"[{self.chain.name}] ❌ Bounce faded: {sym} "
                    f"dropped {change_pct:.1f}% in 20s — aborting entry"
                )
                # Re-add to dip watchlist so it can re-qualify if it recovers
                addr_lower = addr.lower()
                self._dip_watchlist[addr_lower] = {
                    "symbol": sym,
                    "added_at": time.monotonic(),
                    "reason": "bounce_faded",
                    "signal": signal,
                    "risk_level": risk_level,
                }
                return
            if change_pct > 10.0:
                logger.info(
                    f"[{self.chain.name}] ❌ Bounce overshoot: {sym} "
                    f"pumped {change_pct:+.1f}% in 20s — likely ATH, aborting entry"
                )
                return
            logger.info(
                f"[{self.chain.name}] ✅ Bounce confirmed: {sym} "
                f"{change_pct:+.1f}% in 20s — holding 15s to verify price holds"
            )
        else:
            # No price from any source — proceed using check price as baseline.
            # Can't verify fading, so consistent with hold-check behavior (proceeds if unavailable).
            logger.info(
                f"[{self.chain.name}] ⚠️ Bounce price unavailable after 45s: {sym} "
                f"— proceeding with check price ${price_at_check:.6f}"
            )
            current_price = price_at_check

        # ── Second confirmation: wait 15s, ensure price holds within 3% ──
        # Catches dead-cat bounces where we'd buy at the peak of a brief recovery.
        price_at_confirm = current_price
        await asyncio.sleep(15)

        if addr.lower() in self.trader.open_positions:
            logger.info(f"[{self.chain.name}] ⏭ {sym} already held — skipping delayed buy")
            return

        hold_price: float = 0.0
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs") or []
                        chain_pairs = [
                            p for p in pairs
                            if p.get("chainId", "").lower() == self.chain.chain_id
                        ]
                        if chain_pairs:
                            _grad = [p for p in chain_pairs if p.get("dexId", "") != "pump-fun" and p.get("liquidity", {}).get("usd", 0) > 1000]
                            pair = max(
                                _grad or chain_pairs,
                                key=lambda p: p.get("liquidity", {}).get("usd", 0),
                            )
                            hold_price = float(
                                pair.get("priceUsd") or pair.get("price", 0) or 0
                            )
        except Exception as _e:
            logger.debug(f"[{self.chain.name}] Hold-check price fetch error: {_e}")

        if hold_price > 0 and price_at_confirm > 0:
            hold_change_pct = (hold_price - price_at_confirm) / price_at_confirm * 100
            if hold_change_pct < -3.0:
                logger.info(
                    f"[{self.chain.name}] ❌ Hold check failed: {sym} "
                    f"dropped {hold_change_pct:.1f}% after bounce confirm — dead-cat, aborting"
                )
                addr_lower = addr.lower()
                self._dip_watchlist[addr_lower] = {
                    "symbol": sym,
                    "added_at": time.monotonic(),
                    "reason": "bounce_faded",
                    "signal": signal,
                    "risk_level": risk_level,
                }
                return
            logger.info(
                f"[{self.chain.name}] ✅ Hold check passed: {sym} "
                f"{hold_change_pct:+.1f}% — price stable, proceeding with buy"
            )
        # If hold price unavailable, proceed anyway (can't confirm but won't block)

        await self._fire_chart_buy(signal, risk_level)

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
                                     mcap: float = 0.0,
                                     price_change_h1: float = 0.0,
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

        if token_address.lower() in self.trader.open_positions:
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

        # Build a minimal TokenSignal and run through chart dip check
        # before executing — no buy happens on score alone
        signal = TokenSignal(
            token_address=token_address,
            token_symbol=token_symbol,
            token_name=token_symbol,
            chain_id=self.chain.chain_id,
            combined_score=signal_score,
            dex_score=signal_score,
            price_usd=price_usd,
            liquidity_usd=liquidity_usd,
            volume_h1=volume_h1,
            mcap=mcap,
            price_change_h1=price_change_h1,
        )

        risk_level = sec_result.risk_level if not skip_security else "UNKNOWN"
        confirmed = await self._chart_dip_check(signal, risk_level)
        if not confirmed:
            logger.info(
                f"[{self.chain.name}] [{strategy_tag}] 📉 Chart check failed: "
                f"{token_symbol} — waiting for better entry"
            )
            return False

        self.signals_fired += 1
        self._last_buy_time = time.monotonic()
        logger.info(
            f"[{self.chain.name}] [{strategy_tag}] 🎯 BUY SIGNAL: "
            f"{token_symbol} | Score: {signal_score} | {reason}"
        )

        await self._fire_chart_buy(signal, risk_level)
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

    async def _fetch_ohlcv(self, token_address: str, pool_address: str = "") -> Optional[list]:
        """Fetch 30 × 5-minute OHLCV candles via GeckoTerminal (free, no key)."""
        if not token_address:
            return None
        resolved = pool_address.strip() or self._gecko_pool_cache.get(token_address.lower(), "")
        candles = await self._fetch_ohlcv_gecko(token_address, pool_address=resolved)
        return candles

    async def _fetch_ohlcv_gecko(self, token_address: str,
                                   aggregate: str = "5",
                                   limit: int = 30,
                                   pool_address: str = "") -> Optional[list]:
        """Fetch OHLCV candles from GeckoTerminal (free, no key).

        If pool_address is supplied (from signal.pair_address or cache), skips
        the pool-lookup HTTP call entirely. Falls back to lookup if the supplied
        address is rejected by GeckoTerminal.
        """
        try:
            async with aiohttp.ClientSession() as session:
                pool_addr = pool_address.strip()

                if not pool_addr:
                    # Pool lookup — try each pool in the list until we find one
                    # with actual OHLCV data. Migrated tokens (pump.fun → Meteora)
                    # often have the new pool listed first (more volume) but no
                    # candles yet; the established pool is further down the list.
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
                    # Try each pool (up to 5) until one returns candles.
                    candidate_pools = [
                        p.get("attributes", {}).get("address", "")
                        for p in pools[:5]
                    ]
                    for candidate in candidate_pools:
                        if not candidate:
                            continue
                        ohlcv_url = (
                            f"{GECKO_API}/networks/solana/pools/{candidate}/ohlcv/minute"
                        )
                        async with session.get(
                            ohlcv_url,
                            params={"aggregate": aggregate, "limit": str(limit), "currency": "usd"},
                            headers={"Accept": "application/json"},
                            timeout=aiohttp.ClientTimeout(total=8),
                        ) as ohlcv_resp:
                            if ohlcv_resp.status != 200:
                                continue
                            ohlcv_data = await ohlcv_resp.json()
                            candidate_candles = (
                                ohlcv_data.get("data", {})
                                .get("attributes", {})
                                .get("ohlcv_list", [])
                            )
                            if candidate_candles:
                                pool_addr = candidate
                                if len(self._gecko_pool_cache) >= self._gecko_pool_cache_max:
                                    oldest = next(iter(self._gecko_pool_cache))
                                    del self._gecko_pool_cache[oldest]
                                self._gecko_pool_cache[token_address.lower()] = pool_addr
                                return list(reversed(candidate_candles))
                    return None  # No pool in the list had candle data

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
                        # Supplied pool address rejected — clear cache entry and retry with lookup
                        if pool_address.strip():
                            logger.debug(
                                f"[GeckoTerminal] Supplied pool_address rejected "
                                f"(HTTP {resp.status}), falling back to lookup"
                            )
                            self._gecko_pool_cache.pop(token_address.lower(), None)
                            return await self._fetch_ohlcv_gecko(token_address, aggregate, limit)
                        return None
                    data = await resp.json()
                    candles = (
                        data.get("data", {})
                        .get("attributes", {})
                        .get("ohlcv_list", [])
                    )
                    if candles:
                        return list(reversed(candles))
                    # Supplied pool exists but has no OHLCV data yet (new pool /
                    # pool migration — e.g. pump.fun → Meteora AMM V2).
                    # Fall back to a token-address lookup so we can find an
                    # older established pool that already has candle history.
                    if pool_address.strip():
                        self._gecko_pool_cache.pop(token_address.lower(), None)
                        return await self._fetch_ohlcv_gecko(token_address, aggregate, limit)
                    return None
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

    def _compute_chart_score(self, candles: list) -> dict:
        """
        Compute a 0-30 chart quality score from OHLCV candles.
        candles: list of [timestamp, open, high, low, close, volume_usd]
        """
        if len(candles) < 6:
            return {
                "chart_score": 0, "pattern": "insufficient_data",
                "obv_trend": "unknown", "distribution_detected": False,
                "volume_accumulation_ratio": 1.0, "range_compression_pct": 0.0,
                "wick_absorption": 0.0, "vol_below_pct": 0.5,
            }

        def _sf(v):
            try:
                return float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        highs   = [_sf(c[2]) for c in candles]
        lows    = [_sf(c[3]) for c in candles]
        closes  = [_sf(c[4]) for c in candles]
        volumes = [_sf(c[5]) for c in candles]

        def _mean(lst):
            return sum(lst) / len(lst) if lst else 0.0

        # ── Volume Accumulation Ratio (score: -5 to +10) ──────────────────
        recent_vol_avg = _mean(volumes[-3:])
        prior_vols     = volumes[-min(10, len(volumes)):-3]
        prior_vol_avg  = _mean(prior_vols) if prior_vols else (recent_vol_avg or 1.0)
        var = recent_vol_avg / prior_vol_avg if prior_vol_avg > 0 else 1.0

        recent_price_change = (
            (closes[-1] - closes[-4]) / closes[-4] * 100
            if len(closes) >= 4 and closes[-4] > 0 else 0.0
        )
        if var >= 2.0 and -5 <= recent_price_change <= 10:
            var_score = 10
        elif var >= 1.5 and -5 <= recent_price_change <= 15:
            var_score = 5
        elif var < 0.3:
            var_score = -5
        else:
            var_score = 0

        # ── On-Balance Volume trend (score: -8 to +8) ────────────────────
        obv = [0.0]
        for i in range(1, len(closes)):
            if closes[i] > closes[i - 1]:
                obv.append(obv[-1] + volumes[i])
            elif closes[i] < closes[i - 1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])

        obv_recent = _mean(obv[-3:])
        obv_prior  = _mean(obv[-6:-3]) if len(obv) >= 6 else obv[0]
        if obv_recent > obv_prior * 1.1:
            obv_trend = "rising"
            obv_score = 8
        elif obv_recent < obv_prior * 0.9:
            obv_trend = "falling"
            obv_score = -8
        else:
            obv_trend = "flat"
            obv_score = 0

        price_rising = closes[-1] > closes[-4] if len(closes) >= 4 else False
        distribution_detected = (obv_trend == "falling" and price_rising)

        # ── Range Compression (score: -3 to +7) ─────────────────────────
        lookback  = min(6, len(candles))
        range_pct = (
            (max(highs[-lookback:]) - min(lows[-lookback:])) / closes[-1] * 100
            if closes[-1] > 0 else 999.0
        )
        if range_pct <= 5.0 and var >= 1.0:
            compression_score = 7
        elif range_pct <= 10.0:
            compression_score = 3
        elif range_pct > 30.0:
            compression_score = -3
        else:
            compression_score = 0

        # ── Wick Absorption (score: 0 to +5) ────────────────────────────
        wick_ratios = []
        for i in range(-min(3, len(candles)), 0):
            h, l, c = highs[i], lows[i], closes[i]
            candle_range = h - l
            if candle_range > 0:
                wick_ratios.append((c - l) / candle_range)
        avg_wick   = _mean(wick_ratios) if wick_ratios else 0.0
        wick_score = 5 if avg_wick > 0.65 else (2 if avg_wick > 0.50 else 0)

        # ── Volume-Weighted Price Position (score: 0 to +5) ─────────────
        vol_below = sum(v for cv, v in zip(closes, volumes) if cv < closes[-1])
        vol_total = sum(volumes)
        pct_below = vol_below / vol_total if vol_total > 0 else 0.5
        vwpp_score = 5 if pct_below >= 0.70 else (2 if pct_below >= 0.50 else 0)

        # ── Final score ──────────────────────────────────────────────────
        raw         = var_score + max(0, obv_score) + compression_score + wick_score + vwpp_score
        chart_score = max(0, min(30, raw))

        if distribution_detected:
            pattern = "distribution"
        elif range_pct > 30:
            pattern = "chaos"
        elif compression_score >= 5 and var_score >= 5:
            pattern = "accumulation"
        elif compression_score >= 7:
            pattern = "consolidation_base"
        elif var_score >= 10:
            pattern = "breakout_setup"
        else:
            pattern = "neutral"

        return {
            "chart_score":              chart_score,
            "pattern":                  pattern,
            "obv_trend":                obv_trend,
            "distribution_detected":    distribution_detected,
            "volume_accumulation_ratio": var,
            "range_compression_pct":    range_pct,
            "wick_absorption":          avg_wick,
            "vol_below_pct":            pct_below,
        }

    async def _fetch_live_liquidity(self, token_address: str) -> Optional[float]:
        """Fetch current liquidity from DexScreener for a token.
        Returns USD liquidity or None if fetch fails (caller should fail-open on None)."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json(content_type=None)
                    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "solana"]
                    if not pairs:
                        return None
                    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
                    return float((best.get("liquidity") or {}).get("usd") or 0)
        except Exception:
            return None

    async def _chart_dip_check(self, signal: TokenSignal, risk_level: str = "UNKNOWN") -> bool:
        """Fetch 5m+1m candles and run all dip/recovery filters."""
        if not signal.token_address:
            return False

        # Pump cooldown — if this token was recently flagged PUMP DETECTED (h1>15%),
        # block re-entry for 30 min even if DexScreener h1 rolls back to 0%.
        _pump_ts = self._pump_cooldown.get(signal.token_address.lower(), 0)
        if _pump_ts and time.monotonic() - _pump_ts < 1800:
            remaining = int(1800 - (time.monotonic() - _pump_ts))
            logger.info(
                f"[{self.chain.name}] Pump cooldown: {signal.token_symbol} "
                f"— was PUMP DETECTED, blocking for {remaining}s more"
            )
            return False

        # Volume deceleration cooldown — if this token was recently blocked for
        # dying volume, skip chart analysis entirely for 10 min so a brief volume
        # uptick doesn't let it sneak through on the next scan cycle.
        _vd_expiry = self._vol_decel_blocked.get(signal.token_address.lower(), 0)
        if time.monotonic() < _vd_expiry:
            logger.info(
                f"[{self.chain.name}] Vol-decel cooldown: {signal.token_symbol} "
                f"— blocked for {int(_vd_expiry - time.monotonic())}s more"
            )
            return False

        # Register token with real-time signal layer for pattern tracking
        if self.realtime_signal_layer is not None:
            try:
                self.realtime_signal_layer.watch(signal.token_address)
            except Exception:
                pass

        _pool = signal.pair_address.strip() or self._gecko_pool_cache.get(
            signal.token_address.lower(), ""
        )
        candles_5m, candles_1m = await asyncio.gather(
            self._fetch_ohlcv(signal.token_address, pool_address=_pool),
            self._fetch_ohlcv_gecko(signal.token_address, aggregate="1", limit=30, pool_address=_pool),
            return_exceptions=True,
        )
        if isinstance(candles_5m, Exception):
            candles_5m = None
        if isinstance(candles_1m, Exception):
            candles_1m = None

        if not candles_5m or len(candles_5m) < 3:
            # No candle data — pool not yet indexed on GeckoTerminal.
            # All DexScreener-based filters (h1, h6, m5, volume, liq, score) already
            # passed above. Without RSI/VWAP confirmation, cap h1 at 10% to avoid
            # buying into an already-pumped token blind.
            _reason = "no candles" if not candles_5m else f"only {len(candles_5m)} candles"
            if signal.price_change_h1 > 10:
                logger.info(
                    f"[{self.chain.name}] Chart skip (no candles, already pumped): "
                    f"{signal.token_symbol} — h1={signal.price_change_h1:+.1f}% > 10% "
                    f"with no OHLCV to confirm setup"
                )
                return False
            logger.info(
                f"[{self.chain.name}] Chart pass (no candles): {signal.token_symbol} — "
                f"{_reason}, buying on DexScreener signal "
                f"(score {signal.combined_score}, h1={signal.price_change_h1:+.1f}%, "
                f"liq ${signal.liquidity_usd:,.0f})"
            )
            return True

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

        # ── Pump exhaustion: token already 3x+ from its candle-range low ────────
        # A token that has pumped 300%+ is deep in distribution territory — even a
        # 15% dip and recovery doesn't make it a safe entry at that altitude.
        if len(lows) >= 3 and len(highs) >= 3:
            _candle_low  = min(lows)
            _candle_high = max(highs)
            if _candle_low > 0:
                _pump_mag = (_candle_high / _candle_low - 1) * 100
                if _pump_mag > 300:
                    logger.info(
                        f"[{self.chain.name}] Pump exhaustion: {signal.token_symbol} "
                        f"— candle range {_pump_mag:.0f}% (max 300%) — exit liquidity risk"
                    )
                    return False

        # ── Volume deceleration: recent candles drying up candle-by-candle ──────
        # Tightened from the old 15% floor — catches tokens where buyers are
        # quietly leaving even before volume totally collapses.
        if len(volumes) >= 5:
            _recent_vol = sum(volumes[-2:]) / 2
            _prior_vol  = sum(volumes[-5:-2]) / 3
            if _prior_vol > 0 and _recent_vol < _prior_vol * 0.25:
                logger.info(
                    f"[{self.chain.name}] Volume decelerating: {signal.token_symbol} "
                    f"— recent ${_recent_vol:,.0f}/candle vs prior ${_prior_vol:,.0f}/candle "
                    f"({_recent_vol/_prior_vol*100:.0f}%) — buyers leaving"
                )
                # Cool down this token for 10 min — prevents re-entry on a brief
                # volume tick making the ratio look passable again next scan cycle.
                _vd_addr = signal.token_address.lower()
                self._vol_decel_blocked[_vd_addr] = time.monotonic() + 600
                return False

        # ── Chart quality score ──────────────────────────────────────────────
        chart_quality = self._compute_chart_score(candles)
        cq_score      = chart_quality["chart_score"]
        cq_pattern    = chart_quality["pattern"]

        if chart_quality["distribution_detected"]:
            logger.info(
                f"[{self.chain.name}] Distribution blocked: {signal.token_symbol} "
                f"— OBV falling while price rising"
            )
            return False

        if chart_quality["range_compression_pct"] > self.chart_chaos_range_pct:
            logger.info(
                f"[{self.chain.name}] Chaos blocked: {signal.token_symbol} "
                f"— {chart_quality['range_compression_pct']:.0f}% range"
            )
            # Record so fallback path can't bypass this gate
            _addr_l = signal.token_address.lower()
            self._chaos_blocked[_addr_l] = time.monotonic() + 1800  # 30 min
            return False

        if chart_quality["volume_accumulation_ratio"] < self.chart_dead_vol_ratio:
            logger.info(
                f"[{self.chain.name}] Dead volume blocked: {signal.token_symbol} "
                f"— VAR {chart_quality['volume_accumulation_ratio']:.2f}"
            )
            return False

        if cq_score < self.chart_min_score:
            logger.info(
                f"[{self.chain.name}] Weak chart: {signal.token_symbol} "
                f"chart_score={cq_score}/30 ({cq_pattern}) — need {self.chart_min_score}+"
            )
            return False

        logger.info(
            f"[{self.chain.name}] Chart quality: {signal.token_symbol} "
            f"score={cq_score}/30 ({cq_pattern}) "
            f"VAR={chart_quality['volume_accumulation_ratio']:.1f} "
            f"OBV={chart_quality['obv_trend']} "
            f"range={chart_quality['range_compression_pct']:.1f}%"
        )
        signal.chart_score   = cq_score
        signal.chart_pattern = cq_pattern
        # ── End chart quality score ──────────────────────────────────────────

        logger.info(
            f"[{self.chain.name}] Chart {signal.token_symbol}: "
            f"RSI={rsi_str} VWAP={pvwap_str} "
            f"({len(candles)} × 5m candles, {len(closes_1m)} × 1m candles)"
        )

        addr_lower      = signal.token_address.lower()
        watchlist_entry = self._dip_watchlist.get(addr_lower)
        watchlist_peak  = watchlist_entry["peak_price"] if watchlist_entry else None

        # Hard block: RSI parabolic
        if rsi is not None and rsi > 90:
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

        # Dip range check
        candle_peak = max(highs) if highs else 0.0
        peak = candle_peak
        if peak <= 0 or current <= 0:
            logger.info(
                f"[{self.chain.name}] Chart blocked: {signal.token_symbol} — invalid price"
            )
            return False

        dip_pct      = (current - peak) / peak * 100
        in_dip_range = -45.0 <= dip_pct <= -10.0

        if not in_dip_range:
            self._dip_watchlist[addr_lower] = {
                "peak_price": max(candle_peak, watchlist_peak or 0.0),
                "added_at":   (watchlist_entry or {}).get("added_at", time.monotonic()),
                "signal":     signal,
                "risk_level": risk_level,
            }
            logger.info(
                f"[{self.chain.name}] No dip yet: {signal.token_symbol} "
                f"{dip_pct:+.1f}% from peak — watching (need -10% to -45%)"
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
        # bounce_volume is scored but no longer a hard gate — contributes to recovery_score

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

        # Stabilizing moved from hard gate to 7th scored signal.
        # Threshold stays at 5 — so a token can pass without stabilizing if
        # the other 6 signals are strong. Prevents permanent blocks on volatile
        # memecoins that oscillate but are genuinely recovering.
        recovery_signals = {
            "Last green":  last_green,
            "Bounce ≥2%":  bounce_confirmed,
            "Bounce vol":  bounce_volume,
            "RSI reset":   rsi_reset,
            "Higher low":  higher_low,
            "1m momentum": momentum_1m,
            "Stabilizing": stabilizing,
        }
        recovery_score = sum(recovery_signals.values())
        rec_str = " | ".join(
            f"{k}={'✓' if v else '✗'}" for k, v in recovery_signals.items()
        )

        logger.info(
            f"[{self.chain.name}] 🎯 DIP CHECK: {signal.token_symbol} "
            f"{dip_pct:+.1f}% from peak | recovery {recovery_score}/7 [{rec_str}]"
        )

        if recovery_score < 5:
            logger.info(
                f"[{self.chain.name}] Weak recovery: {signal.token_symbol} "
                f"{recovery_score}/6 signals — need 5, watching"
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
            f"{dip_pct:+.1f}% from peak, {recovery_score}/6 recovery"
        )
        self._dip_watchlist.pop(addr_lower, None)
        return True

    async def _fire_chart_buy(self, signal: TokenSignal, risk_level: str):
        """Execute the buy after all dip/chart checks have passed."""
        addr_lower = signal.token_address.lower()
        if addr_lower in self.trader.open_positions or addr_lower in self._pending_buys:
            return
        self._pending_buys.add(addr_lower)

        # Re-validate score at fire time — the signal may have been cached in the dip
        # watchlist or bounce confirmer queue and the score could have drifted below
        # threshold by the time we actually execute.
        if signal.combined_score < self.min_combined_score:
            logger.info(
                f"[{self.chain.name}] ⏭ Stale signal blocked: {signal.token_symbol} "
                f"score {signal.combined_score} < {self.min_combined_score} at fire time"
            )
            return

        # Block unverifiable tokens — can't size a position without MCap
        if not signal.mcap or signal.mcap <= 0:
            logger.info(
                f"[{self.chain.name}] ❌ MCap unverifiable: {signal.token_symbol} "
                f"— blocked (DexScreener returned $0, cannot assess risk)"
            )
            return

        # Block tokens with no name — not yet indexed, nothing to identify them by
        if not signal.token_symbol or signal.token_symbol in ("?", "UNKNOWN", "unknown"):
            logger.info(
                f"[{self.chain.name}] ❌ No symbol: {signal.token_address[:8]}... "
                f"— blocked (token not indexed yet)"
            )
            return

        # Block DANGER-rated tokens — LP not locked or rugcheck flagged high risk
        if risk_level == "DANGER":
            logger.info(
                f"[{self.chain.name}] ❌ DANGER blocked: {signal.token_symbol} "
                f"— rugcheck rated DANGER (LP likely unlocked or high-risk flags)"
            )
            return

        # Vol/MCap ratio — same 1% floor as standard scanner path
        # Catches inflated-mcap new launches where mcap >> actual liquidity/activity
        if signal.mcap > 0 and signal.volume_h1 > 0:
            vol_mcap_ratio = signal.volume_h1 / signal.mcap
            if vol_mcap_ratio < 0.01:
                logger.info(
                    f"[{self.chain.name}] ❌ Dead volume: {signal.token_symbol} "
                    f"vol/mcap={vol_mcap_ratio*100:.2f}% < 1% "
                    f"(vol=${signal.volume_h1:,.0f} mcap=${signal.mcap:,.0f})"
                )
                return

        # Resolve correct-case mint for Jupiter
        if self.chain.chain_id == "solana":
            resolved = self._mint_map.get(signal.token_address.lower()) or signal.token_address
            signal.token_address = resolved

        if signal.chart_score > 0:
            signal.combined_score = min(100, signal.combined_score + signal.chart_score)

        self.signals_fired += 1
        self._last_buy_time = time.monotonic()
        logger.info(
            f"[{self.chain.name}] BUY SIGNAL: {signal.token_symbol} | "
            f"Score: {signal.combined_score} | "
            f"DEX:{signal.dex_score} | "
            f"Chart:{signal.chart_score}/30 ({signal.chart_pattern}) | "
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

        self.trader.reentry.last_h1_pct[signal.token_address.lower()] = signal.price_change_h1
        try:
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
                chain_id=self.chain.chain_id,
                strategy="scanner",
                pair_address=signal.pair_address or "",
                market_cap_usd=signal.mcap,
                age_hours=signal.age_hours,
                volume_h1_usd=signal.volume_h1,
            )
        finally:
            self._pending_buys.discard(signal.token_address.lower())

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

            # Re-entry block removed — score already accounts for price trend.

            if self.tracker and getattr(self.tracker, 'buying_paused', False):
                continue

            try:
                # Re-validate volume before firing — the initial scan path gates on
                # min_volume_h1_usd, but the watchlist path bypasses it.
                # dip_setup tokens are exempt: crash suppresses 1h volume temporarily.
                if (0 < signal.volume_h1 < self.min_volume_h1_usd
                        and "dip_setup" not in signal.flags):
                    logger.info(
                        f"[{self.chain.name}] Volume floor blocked (watchlist): "
                        f"{signal.token_symbol} ${signal.volume_h1:,.0f}/hr "
                        f"< ${self.min_volume_h1_usd:,.0f}/hr"
                    )
                    to_remove.append(addr_lower)
                    continue

                # Re-run security at fire time — the stored risk_level may be stale
                # (token could have been queued before a later security check blocked it)
                _is_micro = signal.mcap > 0 and signal.mcap <= 80_000
                _dex_id_wl = (signal.raw_pair_data or {}).get("dexId", "").lower()
                _is_bc_wl = (_dex_id_wl == "pump-fun") or (
                    self.chain.chain_id == "solana" and _is_micro and _dex_id_wl == ""
                )
                _is_ps_wl = (_dex_id_wl == "pumpswap")
                fresh_sec = await self.security_checker.check(
                    signal.token_address,
                    self.chain.chain_id,
                    signal.token_symbol,
                    micro_cap=_is_micro,
                    bonding_curve=_is_bc_wl,
                    pumpswap=_is_ps_wl,
                )
                if not fresh_sec.passed:
                    self.signals_blocked_security += 1
                    logger.warning(
                        f"[{self.chain.name}] 🛑 Watchlist security re-check blocked "
                        f"{signal.token_symbol} — {fresh_sec.risk_level} (evicting)"
                    )
                    to_remove.append(addr_lower)
                    continue

                risk_level = fresh_sec.risk_level

                passed = await self._chart_dip_check(signal, risk_level)
                if passed:
                    to_remove.append(addr_lower)
                    logger.info(
                        f"[{self.chain.name}] 🔔 Watchlist dip triggered: "
                        f"{signal.token_symbol} — firing buy from poller"
                    )
                    await self._fire_chart_buy(signal, risk_level)
            except Exception as e:
                logger.debug(
                    f"[{self.chain.name}] Watchlist poll error for "
                    f"{signal.token_symbol}: {e}"
                )

        for addr in to_remove:
            self._dip_watchlist.pop(addr, None)

    async def _watchlist_poller(self):
        """Background task: poll dip watchlist tokens for dip entries every 60 seconds."""
        await asyncio.sleep(30)
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

            _graduated = [p for p in chain_pairs if p.get("dexId", "") != "pump-fun" and p.get("liquidity", {}).get("usd", 0) > 1000]
            pair = max(_graduated or chain_pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0))
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
            age_min = int((now - entry.get("added_at", now)) / 60)
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
        # Skip tokens with no market cap data — unverifiable risk
        if not signal.mcap or signal.mcap <= 0:
            return
        entry = {
            "symbol": signal.token_symbol,
            "score": signal.combined_score,
            "mcap": signal.mcap,
            "price": signal.price_usd,
            "reason": reason,
            "timestamp": time.time(),
            "flags": signal.flags,
            "dex_url": signal.dex_url or f"https://dexscreener.com/solana/{signal.token_address}",
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
