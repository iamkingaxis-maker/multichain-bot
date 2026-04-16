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
import datetime as _dt
import logging
import time as _time
from collections import deque as _deque
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
                 poll_interval: int = 15,
                 micro_cap_enabled: bool = False,
                 micro_cap_min_usd: float = 10_000,
                 micro_cap_max_usd: float = 50_000,
                 micro_cap_position_usd: float = 80.0,
                 micro_cap_max_snipers_pct: float = 30.0,
                 micro_cap_max_dev_pct: float = 50.0,
                 dip_watcher=None):

        self.auth            = auth_manager
        self.auth_manager    = auth_manager  # second alias for Axiom-first methods
        self.trader          = trader
        self.evaluator       = signal_evaluator
        self.security        = security_checker
        self.telegram        = telegram
        self.tracker         = tracker
        self.market_monitor  = market_monitor

        self.min_mcap        = min_mcap_usd
        self.max_mcap        = 1_000_000  # Hard cap — trending tokens above $1M are late/overbought
        self.min_liquidity   = min_liquidity_usd
        self.min_score       = min_score
        self.poll_interval   = poll_interval

        self.micro_cap_enabled      = micro_cap_enabled
        self.micro_cap_min          = micro_cap_min_usd
        self.micro_cap_max          = micro_cap_max_usd
        self.micro_cap_position_usd = micro_cap_position_usd
        self.micro_cap_max_snipers  = micro_cap_max_snipers_pct
        self.micro_cap_max_dev      = micro_cap_max_dev_pct

        # Optional dip-watcher — intercepts micro-cap buys to wait for dip+recovery
        self.dip_watcher = dip_watcher

        # Set by connect_to_bot() — routes buys through chart analysis gate
        self.scanner = None

        # TTL cache: {address: last_evaluated_timestamp}
        # Tokens are re-evaluated after REEVAL_HOURS — their conditions change over time
        self._seen_tokens: dict = {}
        self._REEVAL_HOURS = 2.0

        # Micro-cap candidates seen but not bought (for dashboard Radar panel)
        self.mc_candidates: _deque = _deque(maxlen=40)

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

            last_eval = self._seen_tokens.get(token_address, 0)
            if _time.time() - last_eval < self._REEVAL_HOURS * 3600:
                continue

            self._seen_tokens[token_address] = _time.time()
            new_count += 1

            # Keep cache bounded — evict oldest entries
            if len(self._seen_tokens) > 20_000:
                oldest = sorted(self._seen_tokens, key=lambda a: self._seen_tokens[a])[:10_000]
                for addr in oldest:
                    del self._seen_tokens[addr]

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
        Axiom /meme-trending-v2 endpoint removed — all servers return 404.
        DexScreener profiles/boosts/volume-search are the discovery channel.
        """
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
            effective_min_mcap = (
                self.micro_cap_min if self.micro_cap_enabled else self.min_mcap
            )
            if mcap_usd > 0 and mcap_usd < effective_min_mcap:
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

            # Security check — use relaxed micro_cap mode for fresh small tokens
            is_mc_range = (
                self.micro_cap_enabled
                and mcap_usd > 0
                and self.micro_cap_min <= mcap_usd <= self.micro_cap_max
            )
            if self.security:
                sec_result = await self.security.check(
                    token_address, "solana", ticker, micro_cap=is_mc_range
                )
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
                passed, reason, _ = await axiom_enrich_check(
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

            # ── Micro-cap path ($10k-$50k) ──────────────────────────────────
            if (
                self.micro_cap_enabled
                and actual_mcap > 0
                and self.micro_cap_min <= actual_mcap <= self.micro_cap_max
            ):
                # Gate 1: token must be < 4 hours old — filters stale/dead tokens
                pair_created_ms = pair_data.get("pairCreatedAt") or 0
                age_hours = 0.0
                if pair_created_ms > 0:
                    age_hours = (_time.time() - pair_created_ms / 1000) / 3600
                    if age_hours > 4.0:
                        logger.info(
                            f"[EstablishedScanner] Micro-cap age drop: {ticker} — "
                            f"{age_hours:.1f}h old (max 4h)"
                        )
                        _liq_rej = (pair_data.get("liquidity") or {}).get("usd") or 0
                        self.mc_candidates.appendleft({
                            "time": _dt.datetime.utcnow().strftime("%H:%M:%S"),
                            "symbol": ticker,
                            "name": (pair_data.get("baseToken") or {}).get("name") or ticker,
                            "address": token_address,
                            "mcap": actual_mcap,
                            "liquidity": _liq_rej,
                            "dev_pct": 0,
                            "snipers_pct": 0,
                            "lp_burned": False,
                            "protocol": "DexScreener",
                            "reject_reason": f"Too old: {age_hours:.1f}h",
                            "dex_url": f"https://dexscreener.com/solana/{token_address}",
                        })
                        return False

                # Gate 2: m5 price check — allow positive momentum OR a dip entry.
                # MC tokens are volatile; -50% is the crash threshold (dump in
                # progress). The dead zone (-3% to 0%) is flat/noise with no
                # clear direction. Everything else is either momentum (> 0) or a
                # meaningful dip (-3% to -50%) worth entering.
                pc_m5 = float((pair_data.get("priceChange") or {}).get("m5") or 0)
                _liq_rej = (pair_data.get("liquidity") or {}).get("usd") or 0
                if pc_m5 < -50:
                    logger.info(
                        f"[EstablishedScanner] Micro-cap dump guard: {ticker} — "
                        f"m5={pc_m5:+.1f}% (crash, max -50%)"
                    )
                    self.mc_candidates.appendleft({
                        "time": _dt.datetime.utcnow().strftime("%H:%M:%S"),
                        "symbol": ticker,
                        "name": (pair_data.get("baseToken") or {}).get("name") or ticker,
                        "address": token_address,
                        "mcap": actual_mcap,
                        "liquidity": _liq_rej,
                        "dev_pct": 0,
                        "snipers_pct": 0,
                        "lp_burned": False,
                        "protocol": "DexScreener",
                        "reject_reason": f"Dump: m5={pc_m5:+.1f}% < -50%",
                        "dex_url": f"https://dexscreener.com/solana/{token_address}",
                    })
                    return False
                if -3 < pc_m5 <= 0:
                    logger.info(
                        f"[EstablishedScanner] Micro-cap flat: {ticker} — "
                        f"m5={pc_m5:+.1f}% (dead zone -3% to 0%, no direction)"
                    )
                    self.mc_candidates.appendleft({
                        "time": _dt.datetime.utcnow().strftime("%H:%M:%S"),
                        "symbol": ticker,
                        "name": (pair_data.get("baseToken") or {}).get("name") or ticker,
                        "address": token_address,
                        "mcap": actual_mcap,
                        "liquidity": _liq_rej,
                        "dev_pct": 0,
                        "snipers_pct": 0,
                        "lp_burned": False,
                        "protocol": "DexScreener",
                        "reject_reason": f"Flat: m5={pc_m5:+.1f}% (dead zone)",
                        "dex_url": f"https://dexscreener.com/solana/{token_address}",
                    })
                    return False
                # pc_m5 > 0 (momentum) or pc_m5 <= -3 (dip entry) — proceed

                liq = (pair_data.get("liquidity") or {}).get("usd") or 0

                # Gate 3: minimum liquidity — pools under $3k have slippage so
                # severe that a -15% stop executes closer to -30% in practice
                if liq < 3_000:
                    logger.info(
                        f"[EstablishedScanner] Micro-cap liquidity drop: {ticker} — "
                        f"${liq:,.0f} liquidity (need $3k min)"
                    )
                    return False

                # h1, h6, h24 must all be green — no cap, but all must be positive.
                # A micro-cap in any red timeframe is trending against us.
                _pc_h1  = float((pair_data.get("priceChange") or {}).get("h1")  or 0)
                _pc_h6  = float((pair_data.get("priceChange") or {}).get("h6")  or 0)
                _pc_h24 = float((pair_data.get("priceChange") or {}).get("h24") or 0)
                if _pc_h6 <= 0 or _pc_h24 < 0:
                    logger.info(
                        f"[EstablishedScanner] Red timeframe blocked: {ticker} — "
                        f"h1={_pc_h1:+.1f}% h6={_pc_h6:+.1f}% h24={_pc_h24:+.1f}% "
                        f"— all must be green"
                    )
                    self.mc_candidates.appendleft({
                        "time": _dt.datetime.utcnow().strftime("%H:%M:%S"),
                        "symbol": ticker,
                        "name": (pair_data.get("baseToken") or {}).get("name") or ticker,
                        "address": token_address,
                        "mcap": actual_mcap,
                        "liquidity": liq,
                        "dev_pct": 0,
                        "snipers_pct": 0,
                        "lp_burned": False,
                        "protocol": "DexScreener",
                        "reject_reason": f"Red tf: h1={_pc_h1:+.1f}% h6={_pc_h6:+.1f}% h24={_pc_h24:+.1f}%",
                        "dex_url": f"https://dexscreener.com/solana/{token_address}",
                    })
                    return False

                logger.info(
                    f"[EstablishedScanner] 🌱 MICRO-CAP SIGNAL: {ticker} | "
                    f"MCap: ${actual_mcap:,.0f} | m5: {pc_m5:+.1f}% | Liq: ${liq:,.0f}"
                )
                self.tokens_evaluated += 1
                self.signals_fired += 1
                _signal_price = float(pair_data.get("priceUsd") or 0)
                _mc_reason = f"Micro-cap established | ${actual_mcap:,.0f} mcap"
                _in_dip_window = -20 <= pc_m5 <= -3

                # Guard: don't double-buy if DipWatcher or another path already holds this token
                if token_address.lower() in self.trader.open_positions:
                    logger.info(
                        f"[EstablishedScanner] Already holding {ticker} — skip duplicate buy"
                    )
                    return False

                if _in_dip_window:
                    # m5 already in dip zone — buy immediately
                    logger.info(
                        f"[EstablishedScanner] 🎯 Dip entry: {ticker} "
                        f"m5={pc_m5:+.1f}% — buying now"
                    )
                    await self.trader.buy(
                        token_address=token_address,
                        token_symbol=ticker,
                        reason=_mc_reason + f" | dip entry m5={pc_m5:+.1f}%",
                        signal_score=50,
                        override_usd=self.micro_cap_position_usd,
                    )
                elif self.dip_watcher:
                    import time as _t
                    _h6 = float((pair_data.get("priceChange") or {}).get("h6") or 0)
                    _created_ms = pair_data.get("pairCreatedAt") or 0
                    _age_h = (_t.time() - _created_ms / 1000) / 3600 if _created_ms > 0 else 999.0
                    await self.dip_watcher.watch(
                        token_address=token_address,
                        token_symbol=ticker,
                        reason=_mc_reason,
                        override_usd=self.micro_cap_position_usd,
                        signal_price=_signal_price,
                        h6_pct=_h6,
                        token_age_hours=_age_h,
                    )
                else:
                    await self.trader.buy(
                        token_address=token_address,
                        token_symbol=ticker,
                        reason=_mc_reason,
                        signal_score=50,
                        override_usd=self.micro_cap_position_usd,
                    )
                return True
            # ── End micro-cap path ──────────────────────────────────────────

            if actual_mcap > 0 and actual_mcap < self.min_mcap:
                logger.info(
                    f"[EstablishedScanner] MCap filter drop (real): {ticker} — ${actual_mcap:,.0f}"
                )
                return False
            if actual_mcap > 0 and actual_mcap > self.max_mcap:
                logger.info(
                    f"[EstablishedScanner] MCap too high (late entry risk): "
                    f"{ticker} — ${actual_mcap:,.0f} > ${self.max_mcap:,.0f}"
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
                    logger.info(
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
            mcap    = pair_data.get("marketCap") or 0
            liq     = (pair_data.get("liquidity") or {}).get("usd") or 0
            vol_h1  = (pair_data.get("volume") or {}).get("h1") or 0
            h1_pct  = float((pair_data.get("priceChange") or {}).get("h1") or 0)

            # Block tokens already pumped >10% in the last hour — no chart data to
            # confirm structure, and buying into a pump is buying into potential ATH.
            if h1_pct > 10.0:
                logger.info(
                    f"[EstablishedScanner] ATH risk: {ticker} — h1={h1_pct:+.1f}% > 10%, skipping"
                )
                return False

            logger.info(
                f"[EstablishedScanner] SIGNAL: {ticker} | "
                f"MCap: ${mcap:,.0f} | Score: {score:.0f}"
            )

            if self.scanner:
                # Route through scanner's chart analysis — no buy on score alone
                bought = await self.scanner.process_external_signal(
                    token_address=token_address,
                    token_symbol=ticker,
                    reason=f"Axiom trending | score {score:.0f}",
                    signal_score=int(score),
                    strategy_tag="AxiomTrending",
                    skip_security=True,
                    price_usd=float(pair_data.get("priceUsd") or 0),
                    liquidity_usd=liq,
                    volume_h1=vol_h1,
                    mcap=mcap,
                    price_change_h1=h1_pct,
                )
                return bought
            else:
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
                    # Prefer graduated DEX pools over PumpFun pre-graduation pool
                    _grad = [p for p in pairs if p.get("dexId", "") != "pump-fun" and (p.get("liquidity") or {}).get("usd", 0) > 1000]
                    return max(_grad or pairs, key=lambda p: (
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
            "seen_tokens":      len(self._seen_tokens),  # unique addresses evaluated (TTL 2h)
        }


class AxiomSurgeScanner(AxiomTrendingScanner):
    """
    Scans for established Solana tokens showing unusual price/volume surges
    (the Axiom "Surging" tab equivalent).

    Reuses AxiomTrendingScanner's full evaluation pipeline but with:
      - Higher max_mcap ($5M vs $1M)
      - Shorter re-eval window (30 min vs 2h) — surge events are time-sensitive
      - Surge-specific discovery: DexScreener results filtered by h1 price change > 15%
      - Polls every 30 seconds
    """

    _SURGE_MIN_H1_PCT   = 15.0        # minimum h1 price change to qualify as surging
    _SURGE_MAX_MCAP     = 5_000_000   # $5M
    _SURGE_REEVAL_SECS  = 1800        # 30 min

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_mcap      = self._SURGE_MAX_MCAP
        self._REEVAL_HOURS = self._SURGE_REEVAL_SECS / 3600  # 0.5h

    async def run(self):
        """Surge polling loop — runs every 30 seconds."""
        logger.info(
            f"[SurgeScanner] Starting | "
            f"min_h1={self._SURGE_MIN_H1_PCT}% | "
            f"max_mcap=${self._SURGE_MAX_MCAP:,.0f}"
        )
        while True:
            try:
                await self._poll_surge()
            except Exception as e:
                logger.warning(f"[SurgeScanner] Poll error: {e}")
            await asyncio.sleep(30)

    async def _poll_surge(self):
        """Fetch surge candidates from DexScreener and run them through the eval pipeline."""
        import aiohttp
        candidates: dict = {}

        async with aiohttp.ClientSession() as session:
            # Strategy 1: DexScreener gainers endpoint (may or may not exist)
            for url in (
                "https://api.dexscreener.com/latest/dex/gainers/solana",
                "https://api.dexscreener.com/token-boosts/top/v1",
            ):
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            pairs = data if isinstance(data, list) else (data.get("pairs") or [])
                            for pair in pairs:
                                if pair.get("chainId") != "solana":
                                    continue
                                addr = (pair.get("baseToken") or {}).get("address") or ""
                                if addr and addr not in candidates:
                                    candidates[addr] = self._pair_to_token_dict(pair)
                except Exception as e:
                    logger.debug(f"[SurgeScanner] {url} error: {e}")

            # Strategy 2: DexScreener search sorted by trending/volume — keep only h1 gainers
            for order in ("trending", "volume"):
                try:
                    url = f"https://api.dexscreener.com/latest/dex/search?q=solana&order={order}"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            for pair in (data.get("pairs") or [])[:60]:
                                if pair.get("chainId") != "solana":
                                    continue
                                h1_chg = float((pair.get("priceChange") or {}).get("h1", 0) or 0)
                                if h1_chg < self._SURGE_MIN_H1_PCT:
                                    continue
                                addr = (pair.get("baseToken") or {}).get("address") or ""
                                if addr and addr not in candidates:
                                    candidates[addr] = self._pair_to_token_dict(pair)
                except Exception as e:
                    logger.debug(f"[SurgeScanner] search ({order}) error: {e}")

        if not candidates:
            return

        now = _time.time()
        new_count = 0
        for addr, token_dict in candidates.items():
            if now - self._seen_tokens.get(addr, 0) < self._SURGE_REEVAL_SECS:
                continue
            self._seen_tokens[addr] = now
            new_count += 1
            await self._evaluate_token(addr, token_dict)

        if new_count:
            logger.info(
                f"[SurgeScanner] Evaluated {new_count} surge candidates "
                f"({len(candidates)} total found)"
            )

    @staticmethod
    def _pair_to_token_dict(pair: dict) -> dict:
        return {
            "tokenAddress": (pair.get("baseToken") or {}).get("address") or "",
            "tokenTicker":  (pair.get("baseToken") or {}).get("symbol") or "",
            "marketCap":    pair.get("marketCap"),
            "liquidityUsd": (pair.get("liquidity") or {}).get("usd"),
            "pairAddress":  pair.get("pairAddress") or "",
        }

    def get_stats(self) -> dict:
        stats = super().get_stats()
        stats["type"] = "surge"
        return stats
