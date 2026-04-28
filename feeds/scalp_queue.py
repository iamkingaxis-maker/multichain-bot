"""
ScalpQueue (4-phase setup detector) — orchestrator.

Flow every SCAN_INTERVAL seconds:
  1. Refresh global regime: SOL bearish? Majority of watched tokens red?
  2. Discover candidates via DexScreener (passing candidate gates).
  3. For each watched token, pull 5m OHLCV from GeckoTerminal.
  4. Feed candles to the per-token SetupDetector.
  5. On TriggerSignal: apply global no-trade filters, R/R, capital cap.
  6. If clear → trader.buy(strategy='scalp', scalp_meta={...}).
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

import aiohttp

from feeds.axiom_discovery import fetch_axiom_trending_pairs
from feeds.candle_utils import sol_is_bearish
from feeds.gecko_ohlcv import GeckoTerminalClient
from feeds.setup_detector import SetupDetector, TriggerSignal

logger = logging.getLogger(__name__)

_SCAN_INTERVAL = 60    # seconds between orchestrator cycles
_DEX_CHAIN = "solana"
_SOL_POOL = "83v8iPyZihDEjDdY8RdZddyZNyUtXngz69Lgo9Kt5d6d"  # SOL/USDC Raydium


class ScalpQueue:
    def __init__(
        self,
        trader,
        open_positions_ref: dict,
        scalp_capital,
        config,
        ohlcv_client: Optional[GeckoTerminalClient] = None,
        scanner=None,
        auth_manager=None,
    ):
        self.trader = trader
        self.open_positions_ref = open_positions_ref
        self.scalp_capital = scalp_capital
        self.cfg = config
        self.ohlcv = ohlcv_client or GeckoTerminalClient(
            cache_ttl=getattr(config, "scalp_gt_cache_ttl_sec", 60),
            rate_per_min=getattr(config, "scalp_gt_rate_per_min", 25),
        )
        self.scanner = scanner
        self.auth_manager = auth_manager

        # token_address (lower) -> {"symbol", "pool_address", "detector"}
        self._watched: Dict[str, dict] = {}
        # pool_address -> (timestamp_monotonic, liquidity_usd) for rug detection
        self._lp_history: Dict[str, Tuple[float, float]] = {}
        # address (lower) -> monotonic expiry for post-loss cooldown
        self._stop_cooldowns: Dict[str, float] = {}

        # Regime flags refreshed each cycle
        self._sol_is_bearish: bool = False
        self._majority_red: bool = False

    # ── Public entry point ──────────────────────────────────────

    async def run(self):
        logger.info(
            f"[ScalpQueue] Starting — 4-phase detector, "
            f"${self.cfg.scalp_position_usd:.0f}/trade, "
            f"max={self.scalp_capital.max_concurrent} concurrent, "
            f"TP1 +{self.cfg.scalp_tp1_pct}%/{int(self.cfg.scalp_tp1_sell*100)}% "
            f"TP2 +{self.cfg.scalp_tp2_pct}%/{int(self.cfg.scalp_tp2_sell*100)}% "
            f"stop -{self.cfg.scalp_stop_pct}%"
        )
        while True:
            try:
                await self._cycle()
            except Exception as e:
                logger.error(f"[ScalpQueue] Cycle error: {e}")
            await asyncio.sleep(_SCAN_INTERVAL)

    def on_scalp_close(self, addr: str, reason: str, pnl_usd: float = 0.0):
        self.scalp_capital.record_close(addr, pnl_usd)
        # Match the actual reason strings emitted by position_manager._evaluate_scalp:
        # "stop_loss", "scalp_time_exit", "scalp_max_hold". Old strings kept for any
        # legacy callers.
        cooldown_reasons = (
            "stop_loss", "time_exit",
            "scalp_time_exit", "scalp_max_hold",
        )
        if reason in cooldown_reasons:
            expiry = time.monotonic() + self.cfg.scalp_stop_cooldown_minutes * 60
            self._stop_cooldowns[addr.lower()] = expiry

    # ── Orchestrator cycle ──────────────────────────────────────

    async def _cycle(self):
        await self._refresh_regime()
        pairs = await self._fetch_candidates()
        added = 0
        reject_gates = 0
        reject_rug = 0
        reject_cooldown = 0
        reject_open = 0
        for p in pairs:
            addr = (p.get("baseToken") or {}).get("address", "").lower()
            if not addr or addr in self._watched:
                continue
            if not self._passes_candidate_gates(p):
                reject_gates += 1
                continue
            if self._is_rug(p.get("pairAddress", ""), p):
                reject_rug += 1
                continue
            if self._is_on_cooldown(addr):
                reject_cooldown += 1
                continue
            if addr in {a.lower() for a in (self.open_positions_ref or {}).keys()}:
                reject_open += 1
                continue
            if len(self._watched) >= self.cfg.scalp_max_watch_candidates:
                break
            self._watched[addr] = {
                "symbol": (p.get("baseToken") or {}).get("symbol", "?"),
                "pool_address": p.get("pairAddress", ""),
                "detector": SetupDetector(
                    symbol=(p.get("baseToken") or {}).get("symbol", "?"),
                    cfg=self.cfg,
                ),
                "added_ts": time.monotonic(),
                "pair": p,
                "source": p.get("_source") or "dex",
                "last_candle_count": -1,
            }
            self._lp_history[p.get("pairAddress", "")] = (
                time.monotonic(),
                float((p.get("liquidity") or {}).get("usd") or 0),
            )
            added += 1

        # Evaluate each watched token
        signals = 0
        from collections import Counter
        reject_phases: Counter = Counter()
        no_cand_by_src: Counter = Counter()
        cand_buckets: Counter = Counter()  # 0, 1-9, 10-24, 25+
        for addr, meta in list(self._watched.items()):
            fired = await self._evaluate_watched(addr, meta)
            cnt = int(meta.get("last_candle_count", 0))
            if cnt == 0:
                cand_buckets["0"] += 1
            elif cnt < 10:
                cand_buckets["1-9"] += 1
            elif cnt < 25:
                cand_buckets["10-24"] += 1
            else:
                cand_buckets["25+"] += 1
            if fired:
                signals += 1
            else:
                det = meta.get("detector")
                tag = getattr(det, "last_reject", "") or "no_candles"
                bucket = tag.split("(")[0]
                reject_phases[bucket] += 1
                if bucket in ("no_candles", "few_candles") or cnt < 25:
                    no_cand_by_src[meta.get("source", "dex")] += 1

        self._prune_watched()
        self._prune_cooldowns()

        src = getattr(self, "_last_source_counts", {}) or {}
        src_str = (
            f"ax={src.get('axiom', 0)} gt={src.get('gt', 0)} "
            f"srch={src.get('search', 0)} stub={src.get('stub_enrich', 0)}"
        )
        phase_str = " ".join(f"{k}={v}" for k, v in reject_phases.most_common()) or "-"
        cand_str = " ".join(
            f"{k}={cand_buckets.get(k, 0)}" for k in ("0", "1-9", "10-24", "25+")
        )
        nc_src_str = " ".join(f"{k}={v}" for k, v in no_cand_by_src.most_common()) or "-"
        logger.info(
            f"[ScalpQueue] Cycle: pairs={len(pairs)} ({src_str}) "
            f"watch={len(self._watched)}/{self.cfg.scalp_max_watch_candidates} "
            f"(+{added}) signals={signals} "
            f"sol_bear={self._sol_is_bearish} maj_red={self._majority_red} "
            f"| rejects: gate={reject_gates} rug={reject_rug} "
            f"cd={reject_cooldown} open={reject_open} "
            f"| phases: {phase_str} | bars: {cand_str} | no_bars_src: {nc_src_str}"
        )

    async def _refresh_regime(self):
        try:
            sol_candles = await self.ohlcv.fetch_5m(_SOL_POOL, limit=20)
            self._sol_is_bearish = sol_is_bearish(sol_candles) if sol_candles else False
        except Exception as e:
            logger.debug(f"[ScalpQueue] SOL regime fetch failed: {e}")
            self._sol_is_bearish = False

        # Majority-red: compute on the most recent watched pairs snapshot
        if self._watched:
            reds = 0
            total = 0
            for meta in self._watched.values():
                pair = meta.get("pair") or {}
                m5 = (pair.get("priceChange") or {}).get("m5")
                if m5 is None:
                    continue
                total += 1
                if m5 < 0:
                    reds += 1
            self._majority_red = total > 0 and (reds / total) > 0.5
        else:
            self._majority_red = False

    async def _evaluate_watched(self, addr: str, meta: dict) -> bool:
        pool = meta["pool_address"]
        if not pool:
            meta["last_candle_count"] = 0
            return False
        candles = await self.ohlcv.fetch_5m(pool, limit=50)
        meta["last_candle_count"] = len(candles)
        if len(candles) < 25:
            return False
        signal = meta["detector"].evaluate(candles)
        if signal is None:
            return False
        pair = meta.get("pair") or {}
        await self._maybe_fire_entry(addr, pair, signal=signal)
        return True

    # ── Candidate gates ─────────────────────────────────────────

    def _passes_candidate_gates(self, pair: dict) -> bool:
        if (pair.get("chainId") or "").lower() != _DEX_CHAIN:
            return False
        m5_vol = float((pair.get("volume") or {}).get("m5") or 0)
        if m5_vol < self.cfg.scalp_min_m5_volume_usd:
            return False
        liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        if liq < self.cfg.scalp_min_liquidity_usd:
            return False
        return True

    def _is_rug(self, pool: str, pair: dict) -> bool:
        prev = self._lp_history.get(pool)
        if prev is None:
            return False
        prev_ts, prev_liq = prev
        if time.monotonic() - prev_ts > 600:  # history too stale
            return False
        current = float((pair.get("liquidity") or {}).get("usd") or 0)
        if prev_liq <= 0:
            return False
        drop_pct = (prev_liq - current) / prev_liq * 100
        return drop_pct > self.cfg.scalp_rug_lp_drop_pct

    def _is_on_cooldown(self, addr: str) -> bool:
        now = time.monotonic()
        if now < self._stop_cooldowns.get(addr.lower(), 0):
            return True
        if self.scanner is not None:
            sl = getattr(self.scanner, "_sl_cooldown", None)
            if sl and now < sl.get(addr.lower(), 0):
                return True
        return False

    # ── Entry decision ──────────────────────────────────────────

    async def _maybe_fire_entry(self, addr: str, pair: dict, signal: TriggerSignal):
        if self._sol_is_bearish:
            logger.info(
                f"[ScalpQueue] No-trade: SOL bearish — skipping {signal.symbol}"
            )
            return
        if self._majority_red:
            logger.info(
                f"[ScalpQueue] No-trade: majority red — skipping {signal.symbol}"
            )
            return
        if not self.scalp_capital.has_capacity():
            return
        # Capital deployment cap
        deployed = self.scalp_capital.deployed_usd()
        cap_usd = self.scalp_capital.total_capital * self.cfg.scalp_max_deployment_pct
        if deployed + self.cfg.scalp_position_usd > cap_usd:
            logger.info(
                f"[ScalpQueue] Deployment cap hit (${deployed:.0f}/${cap_usd:.0f}) — "
                f"skipping {signal.symbol}"
            )
            return

        now_ts = int(time.time())
        scalp_meta = {
            "sweep_low": signal.sweep_low,
            "stop_price": signal.stop_price,
            "tp1_price": signal.tp1_price,
            "entry_close_time": now_ts,
        }

        logger.info(
            f"[ScalpQueue] ENTRY {signal.symbol} ({addr[:8]}) @ {signal.entry_price:.8f} "
            f"stop={signal.stop_price:.8f} tp1={signal.tp1_price:.8f} | {signal.reason}"
        )
        try:
            await self.trader.buy(
                token_address=addr,
                token_symbol=signal.symbol,
                strategy="scalp",
                override_usd=self.cfg.scalp_position_usd,
                reason=f"scalp-setup: {signal.reason}",
                scalp_meta=scalp_meta,
            )
            if addr.lower() in {a.lower() for a in (self.open_positions_ref or {}).keys()}:
                self.scalp_capital.record_open(addr, self.cfg.scalp_position_usd)
                self._watched.pop(addr, None)
        except Exception as e:
            logger.error(f"[ScalpQueue] Buy failed for {signal.symbol}: {e}")

    # ── Candidate fetch (DexScreener) ───────────────────────────

    async def _fetch_candidates(self) -> List[dict]:
        pairs: List[dict] = []
        seen: set = set()
        stub_addrs: set = set()
        self._last_source_counts: Dict[str, int] = {
            "axiom": 0, "gt": 0, "search": 0, "stub_enrich": 0,
        }

        async def _get_json(session, url):
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.json(content_type=None)
            except Exception as e:
                logger.debug(f"[ScalpQueue] fetch error {url}: {e}")
                return None

        async with aiohttp.ClientSession() as session:
            stub_urls = (
                "https://api.dexscreener.com/token-profiles/latest/v1",
                "https://api.dexscreener.com/token-boosts/active/v1",
                "https://api.dexscreener.com/token-boosts/top/v1",
            )
            search_urls = tuple(
                f"https://api.dexscreener.com/latest/dex/search?q={_DEX_CHAIN}&order={order}"
                for order in ("volume", "trending")
            )

            stub_results, search_results, axiom_pairs, gt_pairs = await asyncio.gather(
                asyncio.gather(*(_get_json(session, u) for u in stub_urls)),
                asyncio.gather(*(_get_json(session, u) for u in search_urls)),
                fetch_axiom_trending_pairs(self.auth_manager),
                self.ohlcv.fetch_trending_pools(
                    pages=getattr(self.cfg, "scalp_gt_trending_pages", 1)
                ),
            )

            for src_name, src_pairs in (("axiom", axiom_pairs or []), ("gt", gt_pairs or [])):
                for p in src_pairs:
                    base = (p.get("baseToken") or {}).get("address", "")
                    if not base or base in seen:
                        continue
                    seen.add(base)
                    pairs.append(p)
                    self._last_source_counts[src_name] += 1

            for data in stub_results:
                if not data:
                    continue
                items = data if isinstance(data, list) else (data.get("tokens") or [])
                for it in items:
                    if it.get("chainId") != _DEX_CHAIN:
                        continue
                    addr = it.get("tokenAddress") or it.get("address") or ""
                    if addr and not addr.startswith("0x") and addr not in seen:
                        stub_addrs.add(addr)

            for data in search_results:
                if not data:
                    continue
                for p in data.get("pairs") or []:
                    if (p.get("chainId") or "").lower() != _DEX_CHAIN:
                        continue
                    base = (p.get("baseToken") or {}).get("address", "")
                    if not base or base.startswith("0x") or base in seen:
                        continue
                    seen.add(base)
                    stub_addrs.discard(base)
                    pairs.append(p)
                    self._last_source_counts["search"] += 1

            if stub_addrs:
                addrs = list(stub_addrs)
                for i in range(0, len(addrs), 30):
                    batch = addrs[i:i + 30]
                    url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                    data = await _get_json(session, url)
                    if not data:
                        continue
                    best: Dict[str, dict] = {}
                    for p in data.get("pairs") or []:
                        if (p.get("chainId") or "").lower() != _DEX_CHAIN:
                            continue
                        base = (p.get("baseToken") or {}).get("address", "")
                        if not base or base in seen:
                            continue
                        liq = float((p.get("liquidity") or {}).get("usd") or 0)
                        cur = best.get(base)
                        if cur is None or liq > float((cur.get("liquidity") or {}).get("usd") or 0):
                            best[base] = p
                    for base, p in best.items():
                        seen.add(base)
                        pairs.append(p)
                        self._last_source_counts["stub_enrich"] += 1

        return pairs

    # ── Maintenance ─────────────────────────────────────────────

    def _prune_watched(self):
        now = time.monotonic()
        expiry_s = self.cfg.scalp_watch_expiry_minutes * 60
        drop = [
            addr for addr, meta in self._watched.items()
            if now - meta["added_ts"] > expiry_s
        ]
        for addr in drop:
            self._watched.pop(addr, None)

    def _prune_cooldowns(self):
        now = time.monotonic()
        self._stop_cooldowns = {
            a: exp for a, exp in self._stop_cooldowns.items() if exp > now
        }
