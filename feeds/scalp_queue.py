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
from collections import deque
from typing import Dict

logger = logging.getLogger(__name__)

_SCAN_INTERVAL = 45  # seconds
_POLL_INTERVAL = 8   # seconds — fast-poll DexScreener for watched set
_POLL_BATCH = 30     # DexScreener tokens/v1 endpoint limit
_DEX_CHAIN = "solana"
_MOMENTUM_MAX_AGE_SEC = 90   # reject pair_momentum data older than this
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
                 config,
                 scanner=None):
        self.trader = trader
        self.axiom_price_feed = axiom_price_feed
        self.open_positions_ref = open_positions_ref
        self.scalp_capital = scalp_capital
        self.cfg = config
        # Optional: scanner's global _sl_cooldown registry. When wired, ANY strategy's
        # close (dip_buy TP, scanner stop, etc) blocks scalp re-entry for 60min+ too.
        self.scanner = scanner

        # addr -> {"symbol", "entry_price", "entry_ts"}
        self._watch: Dict[str, dict] = {}
        # addr -> monotonic expiry timestamp
        self._stop_cooldowns: Dict[str, float] = {}
        # addr (lower) -> {"m5_change", "h1_vol", "m5_buy_ratio", "ts"} — refreshed by poll
        self._pair_momentum: Dict[str, dict] = {}

        # Rolling momentum-gate rejection counters (reset each scan summary)
        self._mg_no_data = 0
        self._mg_m5_low = 0
        self._mg_m5_high = 0
        self._mg_vol_h1 = 0
        self._mg_buy_ratio = 0
        self._mg_avg_trade = 0

        # Quality-gate rejection counters (reset each scan summary)
        self._qg_mcap = 0
        self._qg_age = 0
        self._qg_volume = 0
        self._qg_open = 0
        self._qg_cooldown = 0
        self._qg_full = 0
        self._qg_trend = 0

    async def run(self):
        logger.info("[ScalpQueue] Starting — watching for scalp entries")
        # Axiom WS per-token subscriptions don't deliver prices on our proxy,
        # so fast-poll DexScreener for the watch set to feed the tick gate.
        asyncio.create_task(self._poll_watched_prices())
        while True:
            try:
                self._reconcile_open_slots()
                await self._scan_cycle()
                self._prune_watch_set()
            except Exception as e:
                logger.error(f"[ScalpQueue] Error: {e}")
            await asyncio.sleep(_SCAN_INTERVAL)

    def _reconcile_open_slots(self):
        """Drop phantom entries from scalp_capital._open whose addresses are no
        longer in open_positions_ref. Phantoms accumulate when trader.buy()
        returns without actually opening a position (kill switch, LP unlock,
        swap failure, etc.)."""
        open_refs = self.open_positions_ref or {}
        open_lower = {a.lower() for a in open_refs.keys()}
        phantoms = [
            a for a in list(self.scalp_capital._open.keys())
            if a.lower() not in open_lower
        ]
        if phantoms:
            for a in phantoms:
                self.scalp_capital._open.pop(a, None)
            logger.warning(
                f"[ScalpQueue] Reconciled {len(phantoms)} phantom scalp slot(s); "
                f"now open={len(self.scalp_capital._open)}/"
                f"{self.scalp_capital.max_concurrent}"
            )

    async def _poll_watched_prices(self):
        """Every _POLL_INTERVAL seconds, fetch DexScreener prices for all watched
        tokens and push them into the AxiomPriceFeed tick buffers + cache so the
        existing tick gate logic works without WS messages."""
        poll_count = 0
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                apf = self.axiom_price_feed
                if apf is None or not self._watch:
                    continue
                addrs = list(self._watch.keys())
                total_prices = 0
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as session:
                    for i in range(0, len(addrs), _POLL_BATCH):
                        batch = addrs[i:i + _POLL_BATCH]
                        url = (
                            "https://api.dexscreener.com/tokens/v1/solana/"
                            + ",".join(batch)
                        )
                        try:
                            async with session.get(url) as resp:
                                if resp.status != 200:
                                    continue
                                data = await resp.json()
                        except Exception as e:
                            logger.debug(f"[ScalpQueue] poll batch failed: {e}")
                            continue
                        if not isinstance(data, list):
                            continue
                        now = time.time()
                        for pair in data:
                            base = (pair.get("baseToken") or {}).get("address") or ""
                            if not base:
                                continue
                            try:
                                price_usd = float(pair.get("priceUsd") or 0)
                            except (TypeError, ValueError):
                                price_usd = 0.0
                            if price_usd <= 0:
                                continue
                            addr_l = base.lower()
                            apf.price_cache[addr_l] = price_usd
                            if hasattr(apf, "price_timestamps"):
                                apf.price_timestamps[addr_l] = now
                            buf = apf._tick_buffers.setdefault(
                                addr_l, deque(maxlen=600)
                            )
                            buf.append((now, price_usd))
                            total_prices += 1

                            # Capture momentum fields for the gate
                            pc = pair.get("priceChange") or {}
                            vol = pair.get("volume") or {}
                            m5_txn = (pair.get("txns") or {}).get("m5") or {}
                            try:
                                buys = float(m5_txn.get("buys") or 0)
                                sells = float(m5_txn.get("sells") or 0)
                            except (TypeError, ValueError):
                                buys = sells = 0.0
                            total_txn = buys + sells
                            m5_vol = float(vol.get("m5") or 0)
                            self._pair_momentum[addr_l] = {
                                "m5_change": float(pc.get("m5") or 0),
                                "h1_vol": float(vol.get("h1") or 0),
                                "m5_vol": m5_vol,
                                "m5_buy_ratio": (buys / total_txn) if total_txn > 0 else 0.0,
                                "m5_txns": int(total_txn),
                                # Avg USD per m5 trade — defeats bot-spam ratios
                                # (100 tiny buys + 10 real sells would otherwise
                                # read as 91% buy-biased).
                                "m5_avg_trade_usd": (m5_vol / total_txn) if total_txn > 0 else 0.0,
                                "ts": now,
                            }
                poll_count += 1
                if poll_count % 8 == 1:
                    logger.info(
                        f"[ScalpQueue] Poll: pushed {total_prices} prices "
                        f"to tick buffers for {len(addrs)} watched tokens"
                    )
            except Exception as e:
                logger.error(f"[ScalpQueue] poll error: {e}")

    def on_scalp_close(self, addr: str, reason: str, pnl_usd: float = 0.0):
        """Called by PositionManager on every scalp position close."""
        self.scalp_capital.record_close(addr, pnl_usd)
        if reason == "stop_loss":
            expiry = time.monotonic() + self.cfg.scalp_stop_cooldown_minutes * 60
            # Normalize to lowercase — PM passes addr after trader lowercased it,
            # but DexScreener scan hands mixed-case addrs to the gate. Keep one key.
            self._stop_cooldowns[addr.lower()] = expiry
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
                # Set when m5 first enters dip sweet spot [min_m5, max_m5]. Remains
                # None while the token is "not red enough" — so we can evict
                # range-bound tokens that never dip (warmup timeout) without
                # touching tokens actively oscillating through the sweet spot.
                "entered_sweet_spot_ts": None,
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
            await self._check_momentum_gate(addr)

        logger.info(
            f"[ScalpQueue] Scan: {len(pairs)} pairs (non-sol filtered={non_sol}) → "
            f"+{added} watched (total watch={len(self._watch)}/"
            f"{self.cfg.scalp_max_watch_candidates}) | "
            f"quality-gate rejects: mcap={self._qg_mcap} age={self._qg_age} "
            f"volume={self._qg_volume} trend={self._qg_trend} open={self._qg_open} "
            f"cooldown={self._qg_cooldown} full={self._qg_full} | "
            f"momentum-gate rejects: no_data={self._mg_no_data} "
            f"m5_low={self._mg_m5_low} m5_high={self._mg_m5_high} "
            f"vol_h1={self._mg_vol_h1} buy_ratio={self._mg_buy_ratio} "
            f"avg_trade={self._mg_avg_trade}"
        )
        self._mg_no_data = 0
        self._mg_m5_low = 0
        self._mg_m5_high = 0
        self._mg_vol_h1 = 0
        self._mg_buy_ratio = 0
        self._mg_avg_trade = 0
        self._qg_mcap = 0
        self._qg_age = 0
        self._qg_volume = 0
        self._qg_open = 0
        self._qg_cooldown = 0
        self._qg_full = 0
        self._qg_trend = 0

    def _is_on_cooldown(self, addr: str) -> bool:
        """Return True if addr is on EITHER the local scalp cooldown or the
        scanner's global _sl_cooldown. Ensures a close from any strategy
        (dip_buy, scanner, scalp) blocks scalp re-entry uniformly."""
        addr_l = addr.lower()
        now = time.monotonic()
        if now < self._stop_cooldowns.get(addr_l, 0):
            return True
        if self.scanner is not None:
            sl = getattr(self.scanner, "_sl_cooldown", None)
            if sl and now < sl.get(addr_l, 0):
                return True
        return False

    def _passes_quality_gates(self, pair: dict, addr: str) -> bool:
        # Solana-only — DexScreener /search ignores chain= filter and returns
        # multi-chain pairs. ETH/BSC addresses will never populate an Axiom
        # Solana tick feed, so filter them out at the source.
        if (pair.get("chainId") or "").lower() != _DEX_CHAIN:
            return False
        if addr.startswith("0x"):
            return False

        # open_positions_ref is keyed lowercase (trader stores via .lower()) but
        # DexScreener addresses arrive mixed-case. Normalize before lookup or the
        # gate silently misses and re-queues tokens we already hold.
        if addr.lower() in self.open_positions_ref:
            self._qg_open += 1
            return False
        if self._is_on_cooldown(addr):
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

        # Trend filter: reject tokens that are either in a downtrend OR already
        # parabolic. h6/h24 <0 = bleed (m5 pumps mean-revert against us).
        # h6/h24 too high = exhausted / distribution phase (m5 dips more likely
        # to be whales rotating out than healthy pullbacks).
        pc = pair.get("priceChange") or {}
        try:
            h6 = float(pc.get("h6") or 0)
            h24 = float(pc.get("h24") or 0)
        except (TypeError, ValueError):
            h6 = h24 = 0.0
        if h6 < 0 or h24 < 0:
            self._qg_trend += 1
            return False
        if h6 > self.cfg.scalp_max_h6_change_pct or h24 > self.cfg.scalp_max_h24_change_pct:
            self._qg_trend += 1
            return False

        return True

    # ── Momentum gate (DexScreener-based) ───────────────────────

    async def _check_momentum_gate(self, addr: str):
        """Fire entry when DexScreener shows real recent momentum: m5 change
        in the sweet spot, non-trivial h1 volume, and buy-biased m5 txns.
        Replaces the broken poll-tick gate (ratio always = 1.00, ticks = noise)."""
        if addr not in self._watch:
            return

        meta = self._watch[addr]
        symbol = meta["symbol"]

        if addr in self.open_positions_ref or addr.lower() in self.open_positions_ref:
            del self._watch[addr]
            return

        # Defense-in-depth: cooldown may have been set while addr was already in
        # _watch (scan quality-gate only checks on ADD). Evict stale watchers.
        # Checks BOTH local scalp cooldown and scanner's global _sl_cooldown.
        if self._is_on_cooldown(addr):
            del self._watch[addr]
            return

        if not self.scalp_capital.has_capacity():
            return

        mom = self._pair_momentum.get(addr) or self._pair_momentum.get(addr.lower())
        if not mom or (time.time() - mom.get("ts", 0)) > _MOMENTUM_MAX_AGE_SEC:
            # Treat stale/missing momentum as no_data so we don't fire on data
            # captured before a prior entry+stop cycle.
            self._mg_no_data += 1
            return

        # Gate 1: m5 must be RED within dip-buy sweet spot — we're catching
        # short-term pullbacks on healthy tokens, not chasing green candles.
        m5 = mom["m5_change"]
        if m5 < self.cfg.scalp_min_m5_change_pct:
            # Below capitulation floor — falling knife / likely rug. Evict.
            self._mg_m5_low += 1
            del self._watch[addr]
            return
        if m5 > self.cfg.scalp_max_m5_change_pct:
            # Not red enough yet — stay on watch, dip may deepen next poll.
            self._mg_m5_high += 1
            return

        # Token is inside the dip sweet spot — mark it so prune knows this
        # watcher is "active" and shouldn't be evicted by the warmup timeout.
        meta["entered_sweet_spot_ts"] = time.monotonic()

        # Gate 2: real h1 volume (not just total 24h)
        if mom["h1_vol"] < self.cfg.scalp_min_volume_h1_usd:
            self._mg_vol_h1 += 1
            return

        # Gate 3: buy/sell ratio from actual m5 transactions (not synthesized)
        if mom["m5_buy_ratio"] < self.cfg.scalp_min_m5_buy_ratio:
            self._mg_buy_ratio += 1
            return

        # Gate 4: defeat bot-spam — require meaningful avg trade size. A 91%
        # buy ratio from 100 $5 buys + 10 $500 sells is bots faking demand
        # while whales distribute; average trade size collapses this pattern.
        if mom.get("m5_avg_trade_usd", 0) < self.cfg.scalp_min_m5_avg_trade_usd:
            self._mg_avg_trade += 1
            return

        # All gates passed — fire entry
        logger.info(
            f"[ScalpQueue] ENTRY {symbol} ({addr[:8]}) "
            f"m5={m5:+.2f}% h1_vol=${mom['h1_vol']:,.0f} "
            f"buy_ratio={mom['m5_buy_ratio']:.2f} txns={mom['m5_txns']} "
            f"avg_trade=${mom.get('m5_avg_trade_usd', 0):.0f}"
        )
        del self._watch[addr]

        try:
            await self.trader.buy(
                token_address=addr,
                token_symbol=symbol,
                strategy="scalp",
                override_usd=self.cfg.scalp_position_usd,
                reason=(
                    f"scalp: m5={m5:+.2f}% h1_vol=${mom['h1_vol']:,.0f} "
                    f"buy_ratio={mom['m5_buy_ratio']:.2f}"
                ),
            )
            # trader.buy() silently returns on many early-exit paths (kill
            # switch, LP unlock block, swap failure, etc.) without raising.
            # Only record the slot if a real position was actually created.
            if addr.lower() in {a.lower() for a in (self.open_positions_ref or {}).keys()}:
                self.scalp_capital.record_open(addr, self.cfg.scalp_position_usd)
                self._stop_cooldowns.pop(addr.lower(), None)
            else:
                logger.info(
                    f"[ScalpQueue] {symbol}: trader.buy returned without opening "
                    f"position — slot not recorded"
                )
        except Exception as e:
            logger.error(f"[ScalpQueue] Buy failed for {symbol}: {e}")

    # ── Watch set maintenance ───────────────────────────────────

    def _prune_watch_set(self):
        now_mono = time.monotonic()
        expiry_secs = self.cfg.scalp_watch_expiry_minutes * 60
        warmup_secs = self.cfg.scalp_watch_warmup_minutes * 60

        # Drop rules:
        #   1) Full expiry — any watcher older than scalp_watch_expiry_minutes
        #   2) Warmup timeout — watcher older than scalp_watch_warmup_minutes
        #      that has NEVER entered the dip sweet spot (range-bound tokens
        #      oscillating near flat). Frees slots for fresher candidates.
        to_drop = [
            addr for addr, meta in self._watch.items()
            if now_mono - meta["entry_ts"] > expiry_secs
            or (
                meta.get("entered_sweet_spot_ts") is None
                and now_mono - meta["entry_ts"] > warmup_secs
            )
        ]
        apf = self.axiom_price_feed
        for addr in to_drop:
            logger.debug(f"[ScalpQueue] Expired: {self._watch[addr]['symbol']}")
            del self._watch[addr]
            # Stop streaming prices for expired tokens unless we actually hold them
            if apf is not None and hasattr(apf, "unsubscribe_token") \
                    and addr.lower() not in self.open_positions_ref:
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
