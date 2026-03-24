"""
Established Token Scanner — Phase 1
Polls DexScreener's token-profiles and token-boosts endpoints every 60 seconds.
These cover featured/boosted tokens that don't appear in the standard trending
search — complementary to MultiSourceScanner, not redundant.

Note: Axiom's REST trending endpoint (/meme-trending) was probed across all
API servers and path variants — all return 404. It appears to be removed.
We use DexScreener profiles/boosts as the established-token discovery channel.
"""

import asyncio
import logging
import time as _time
from typing import Optional

logger = logging.getLogger(__name__)


class AxiomTrendingScanner:
    """
    Polls Axiom's trending token endpoint every `poll_interval` seconds
    and evaluates each token through the full signal pipeline.

    Trending tokens are graduated/established — they always have
    DexScreener data. A missing DexScreener result is a warning, not
    a normal skip.
    """

    def __init__(self,
                 auth_manager,
                 trader,
                 signal_evaluator,
                 security_checker,
                 telegram,
                 tracker,
                 market_monitor=None,
                 min_mcap_usd: float = 50_000,
                 min_liquidity_usd: float = 5_000,
                 min_score: float = 65.0,
                 poll_interval: int = 60):

        self.auth            = auth_manager
        self.auth_manager    = auth_manager  # second alias for Axiom-first methods
        self.trader          = trader
        self.evaluator       = signal_evaluator
        self.security        = security_checker
        self.telegram        = telegram
        self.tracker         = tracker
        self.market_monitor  = market_monitor

        self.min_mcap        = min_mcap_usd
        self.min_liquidity   = min_liquidity_usd
        self.min_score       = min_score
        self.poll_interval   = poll_interval

        self._seen_tokens: set = set()

        # Stats
        self.tokens_polled    = 0
        self.tokens_evaluated = 0
        self.signals_fired    = 0
        self._poll_count      = 0

    async def run(self):
        """Main polling loop. Runs forever with exponential backoff on errors."""
        logger.info(
            f"[EstablishedScanner] Starting | "
            f"poll_interval={self.poll_interval}s | "
            f"min_mcap=${self.min_mcap:,.0f} | "
            f"min_score={self.min_score}"
        )

        _backoff = self.poll_interval

        while True:
            try:
                await self._poll_once()
                _backoff = self.poll_interval  # reset on success
                await asyncio.sleep(self.poll_interval)
            except Exception as e:
                logger.warning(
                    f"[EstablishedScanner] Poll error — retrying in {_backoff}s: {e}"
                )
                await asyncio.sleep(_backoff)
                _backoff = min(_backoff * 2, 300)  # max 5 min backoff

    # DexScreener endpoints for established token discovery
    # These are different from MultiSourceScanner's trending search:
    #   - token-profiles: tokens with paid DexScreener profile pages (project teams)
    #   - token-boosts: tokens actively boosted/promoted right now
    _DS_PROFILE_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
    _DS_BOOST_URL   = "https://api.dexscreener.com/token-boosts/active/v1"

    async def _poll_once(self):
        """Fetch established tokens and evaluate new ones."""
        import aiohttp

        # Axiom-first: merge trending tokens before DexScreener polling
        axiom_tokens = await self._fetch_axiom_trending()
        tokens_by_address: dict = {**axiom_tokens}

        async with aiohttp.ClientSession() as session:
            for url in (self._DS_PROFILE_URL, self._DS_BOOST_URL):
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            items = data if isinstance(data, list) else (data.get("tokens") or [])
                            for item in items:
                                # Only Solana tokens
                                if item.get("chainId") != "solana":
                                    continue
                                addr = item.get("tokenAddress") or item.get("address") or ""
                                if addr and addr not in tokens_by_address:
                                    tokens_by_address[addr] = item
                except Exception as e:
                    logger.debug(f"[EstablishedScanner] {url} error: {e}")

        # Also poll DexScreener's top-volume Solana pairs (not in trending search)
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://api.dexscreener.com/latest/dex/search?q=solana&order=volume"
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        for pair in (data.get("pairs") or [])[:30]:
                            if pair.get("chainId") != "solana":
                                continue
                            addr = (pair.get("baseToken") or {}).get("address") or ""
                            if addr and addr not in tokens_by_address:
                                tokens_by_address[addr] = {
                                    "tokenAddress": addr,
                                    "tokenTicker": (pair.get("baseToken") or {}).get("symbol"),
                                    "marketCap": pair.get("marketCap"),
                                    "liquidityUsd": (pair.get("liquidity") or {}).get("usd"),
                                }
        except Exception as e:
            logger.debug(f"[EstablishedScanner] volume search error: {e}")

        tokens = list(tokens_by_address.values())

        self._poll_count += 1
        polled = len(tokens)
        new_count = 0
        signals_this_poll = 0

        for token_dict in tokens:
            self.tokens_polled += 1
            token_address = (
                token_dict.get("tokenAddress") or
                token_dict.get("token_address") or
                token_dict.get("address") or ""
            )
            if not token_address:
                continue

            if token_address in self._seen_tokens:
                continue

            self._seen_tokens.add(token_address)
            new_count += 1

            # Keep seen set bounded
            if len(self._seen_tokens) > 20_000:
                self._seen_tokens = set(list(self._seen_tokens)[-10_000:])

            fired = await self._evaluate_token(token_address, token_dict)
            if fired:
                signals_this_poll += 1
                self.signals_fired += 1

        logger.info(
            f"[EstablishedScanner] Poll #{self._poll_count} — "
            f"polled={polled} | new={new_count} | signals={signals_this_poll}"
        )

    async def _fetch_axiom_trending(self) -> dict:
        """
        Try get_trending_tokens('1h'). Returns {token_address: token_dict} or {} on failure.
        Returns a dict keyed by address so it can be merged into tokens_by_address in _poll_once.
        """
        if not self.auth_manager:
            return {}
        try:
            from axiomtradeapi import AxiomTradeClient  # noqa — just checking available
        except ImportError:
            return {}
        try:
            token_valid = await self.auth_manager.ensure_valid_token()
            if not token_valid:
                return {}
            client = self.auth_manager.get_client()
            if not client:
                return {}
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, client.get_trending_tokens, "1h")
            tokens = []
            if isinstance(data, list):
                tokens = data
            elif isinstance(data, dict):
                tokens = data.get("tokens") or data.get("data") or []
            result = {}
            for t in tokens:
                addr = t.get("tokenAddress") or t.get("address") or ""
                if addr:
                    result[addr] = t
            if result:
                logger.info(f"[AxiomTrending] Axiom trending: {len(result)} tokens")
            return result
        except Exception as e:
            logger.debug(f"[AxiomTrending] Axiom trending unavailable (DexScreener fallback): {e}")
            return {}

    async def _evaluate_token(self, token_address: str, token_dict: dict) -> bool:
        """
        Run full evaluation pipeline for a trending token.
        Returns True if a signal fired.
        """
        ticker = (
            token_dict.get("tokenTicker") or
            token_dict.get("token_ticker") or
            token_dict.get("symbol") or "?"
        )
        try:
            # Market condition gate
            if self.market_monitor and self.market_monitor.market_restricted:
                if not self.market_monitor.should_trade(signal_score=0):
                    return False

            # MCap pre-filter from trending data
            mcap_usd = float(
                token_dict.get("marketCap") or
                token_dict.get("market_cap") or
                token_dict.get("marketCapUsd") or 0
            )
            if mcap_usd > 0 and mcap_usd < self.min_mcap:
                logger.debug(
                    f"[EstablishedScanner] MCap filter drop: {ticker} — ${mcap_usd:,.0f}"
                )
                return False

            liq_usd = float(
                token_dict.get("liquidityUsd") or
                token_dict.get("liquidity_usd") or
                token_dict.get("liquidity") or 0
            )
            if liq_usd > 0 and liq_usd < self.min_liquidity:
                logger.debug(
                    f"[EstablishedScanner] Liquidity filter drop: {ticker} — ${liq_usd:,.0f}"
                )
                return False

            # Security check
            if self.security:
                sec_result = await self.security.check(token_address, "solana")
                if sec_result and not sec_result.passed:
                    logger.info(
                        f"[EstablishedScanner] Security blocked: {ticker} — "
                        f"{sec_result.risk_level}"
                    )
                    return False

            # Enrichment check (holder concentration + dev history)
            from feeds.axiom_scanner import axiom_enrich_check
            pair_address = (
                token_dict.get("pairAddress") or
                token_dict.get("pair_address") or ""
            )
            deployer = (
                token_dict.get("deployer") or
                token_dict.get("deployer_address") or
                token_dict.get("devAddress") or ""
            )
            if pair_address:
                passed, reason = await axiom_enrich_check(
                    self.auth, pair_address, deployer
                )
                if not passed:
                    logger.info(
                        f"[EstablishedScanner] Enrich blocked: {ticker} — {reason}"
                    )
                    return False

            # DexScreener fetch — trending tokens ALWAYS have data
            pair_data = await self._fetch_dexscreener_pair(token_address)
            if pair_data is None:
                logger.warning(
                    f"[EstablishedScanner] No DexScreener data for trending token "
                    f"{ticker} ({token_address[:8]}…) — unexpected, skipping"
                )
                return False

            # Resolve real ticker from pair data (profiles/boosts don't include it)
            real_ticker = (pair_data.get("baseToken") or {}).get("symbol") or ticker
            if real_ticker and real_ticker != "?":
                ticker = real_ticker

            # Hard MCap check using real DexScreener data (Axiom API tokens often lack marketCap)
            actual_mcap = float(pair_data.get("marketCap") or 0)
            if actual_mcap > 0 and actual_mcap < self.min_mcap:
                logger.debug(
                    f"[EstablishedScanner] MCap filter drop (real): {ticker} — ${actual_mcap:,.0f}"
                )
                return False

            # Minimum age check — skip tokens younger than 1 hour (rug-prone new launches)
            pair_created_ms = pair_data.get("pairCreatedAt") or 0
            if pair_created_ms > 0:
                age_hours = (_time.time() - pair_created_ms / 1000) / 3600
                if age_hours < 1.0:
                    logger.debug(
                        f"[EstablishedScanner] Age filter drop: {ticker} — "
                        f"{age_hours*60:.0f}min old (need 60min)"
                    )
                    return False

            self.tokens_evaluated += 1

            # Full signal evaluation
            if self.evaluator:
                evaluation = await self.evaluator.evaluate(pair_data)
                if evaluation.hard_skip:
                    logger.debug(
                        f"[EstablishedScanner] Hard skip: {ticker} — "
                        f"{', '.join(evaluation.skip_reasons)}"
                    )
                    return False
                score = evaluation.total_score
                effective_min = self.min_score
                if self.market_monitor and self.market_monitor.market_restricted:
                    effective_min = self.market_monitor.restricted_threshold
                if score < effective_min:
                    return False
            else:
                score = 70  # default when no evaluator

            # Signal fires
            mcap   = pair_data.get("marketCap") or 0
            liq    = (pair_data.get("liquidity") or {}).get("usd") or 0
            vol_h1 = (pair_data.get("volume") or {}).get("h1") or 0
            logger.info(
                f"[EstablishedScanner] SIGNAL: {ticker} | "
                f"MCap: ${mcap:,.0f} | Score: {score:.0f}"
            )

            await self.telegram.send(
                f"📈 *Axiom Trending Signal* [Solana]\n\n"
                f"🪙 ${ticker}\n"
                f"📊 MCap: ${mcap:,.0f}\n"
                f"💧 Liquidity: ${liq:,.0f}\n"
                f"📈 Volume 1h: ${vol_h1:,.0f}\n"
                f"⭐ Score: {score:.0f}/100\n"
                f"⚡ Source: Axiom 1h Trending"
            )

            await self.trader.buy(
                token_address=token_address,
                token_symbol=ticker,
                reason=f"Axiom trending (1h) | score {score:.0f}",
                signal_score=int(score),
                hh_hl_confirmed=getattr(evaluation, "hh_hl_confirmed", False)
                if self.evaluator else False
            )
            return True

        except Exception as e:
            logger.error(f"[EstablishedScanner] Evaluate error for {ticker}: {e}")
            return False

    async def _fetch_dexscreener_pair(self, token_address: str) -> Optional[dict]:
        """Fetch DexScreener pair data for a token. Returns best Solana pair or None."""
        import aiohttp
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=6)
                ) as resp:
                    data = await resp.json(content_type=None)
                    pairs = [
                        p for p in (data.get("pairs") or [])
                        if p.get("chainId") == "solana"
                    ]
                    if not pairs:
                        return None
                    return max(pairs, key=lambda p: (
                        p.get("liquidity", {}).get("usd") or 0
                    ))
        except Exception as e:
            logger.debug(
                f"[EstablishedScanner] DexScreener fetch failed for "
                f"{token_address[:8]}: {e}"
            )
            return None

    def get_stats(self) -> dict:
        return {
            "polls":            self._poll_count,
            "tokens_polled":    self.tokens_polled,
            "tokens_evaluated": self.tokens_evaluated,
            "signals_fired":    self.signals_fired,
            "seen_tokens":      len(self._seen_tokens),
        }
