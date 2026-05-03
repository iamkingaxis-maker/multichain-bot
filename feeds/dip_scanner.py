"""
DipScanner — buys established Solana tokens dipping within an uptrend.

Entry criteria:
  - Market cap >= $1M and <= $100M (configurable)
  - Pair age >= 7 days
  - 24h volume >= $200k (steady / high activity)
  - 24h price change > 0  (uptrend intact)
  - 1h price change < 0 OR 5m price change < 0  (dip in progress)
  - h6 buy/sell txn ratio >= 1.3 (accumulation, not distribution)
  - Not already in open positions

Sources: DexScreener REST + GeckoTerminal trending pools (both free, no API key).
"""

import asyncio
import json
import logging
import os
import time
import aiohttp
from collections import Counter, deque
from typing import Optional, Dict, Deque, Tuple, List

from feeds.gecko_ohlcv import GeckoTerminalClient

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
                 max_mcap: float = 100_000_000,
                 min_age_days: float = 7.0,
                 min_volume_h24: float = 200_000,
                 max_concurrent: int = 3,
                 min_txn_ratio_h6: float = 1.3,
                 min_vol_h1_ratio: float = 0.5,
                 require_vol_m5: bool = True,
                 min_turnover_h24: float = 2.0,
                 baseline_mode: bool = False,
                 gt_client: Optional[GeckoTerminalClient] = None):
        self.trader = trader
        self.telegram = telegram
        self.open_positions_ref = open_positions_ref
        self.position_usd = position_usd
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_age_ms = min_age_days * 86_400 * 1000  # convert to ms
        self.min_volume_h24 = min_volume_h24
        self.max_concurrent = max_concurrent
        self.min_txn_ratio_h6 = min_txn_ratio_h6
        self.min_vol_h1_ratio = min_vol_h1_ratio
        self.require_vol_m5 = require_vol_m5
        self.min_turnover_h24 = min_turnover_h24
        # Baseline data-collection mode — bypasses heuristic filter `continue`
        # statements while still computing/logging each filter's verdict. Only
        # basic sanity gates (mcap, age, vol_h24, vol_m5_zero, already_open,
        # loss_cooldown, max_concurrent) still enforce. Use under PAPER_MODE
        # to gather a full population sample for shadow-feature validation.
        self.baseline_mode = bool(baseline_mode)
        if self.baseline_mode:
            logger.warning(
                "[DipScanner] BASELINE MODE ENABLED — heuristic filters bypassed. "
                "All sanity-passing dip signals will fire. Paper mode strongly recommended."
            )
        # GT trending pools widen the universe beyond DexScreener stubs/searches.
        # Lazy-init so tests can construct without pulling the feeds.gecko deps.
        # rate_per_min bumped 15 -> 30 (2026-05-03): with baseline mode
        # firing ~10 signals/cycle and each signal pulling 1m+5m+15m+trades
        # (~40 GT calls/cycle), the 15/min budget caused ~50% of structural
        # features (1m_*, 5m_*, vwap) to be missing on entry_meta. 60s cache
        # absorbs the burstiness; 30/min is well under GT's documented free
        # tier ceiling and the chart_reader benefits from cache hits.
        self.gt_client = gt_client or GeckoTerminalClient(cache_ttl=60, rate_per_min=30)

        self._start_monotonic = time.monotonic()
        self.signals_fired = 0
        self._last_buy_time = 0.0
        self._rejected_distribution = 0
        # BTC kline cache for regime tagging (Binance 1h klines, 60s TTL).
        self._btc_cache: Tuple[float, list] = (0.0, [])
        # Memecoin sector breadth cache (CoinGecko categories, 60s TTL).
        # Tier-1 priority from claude-ideas: "if SOL is up but memecoin
        # sector is dumping, dip-buys still die." Solana-meme category
        # preferred over generic meme-token if both available.
        self._meme_cache: Tuple[float, Optional[dict]] = (0.0, None)
        # h24 history per token for trend-reversal detection.  Each scan cycle
        # appends (wall_ts, pc_h24) for every evaluated token; entries older
        # than 6h are pruned.  Used to reject entries where h24 has collapsed
        # to < 25% of recent peak (the meme is dying — see mexicanunc 04-25).
        # Persisted to /data/h24_history.json so the filter survives deploys.
        # 4-tuple per entry: (ts, pc_h24, pc_h1 or None, pc_h6 or None)
        self._h24_history: Dict[str, Deque[Tuple[float, float, Optional[float], Optional[float]]]] = {}
        self._h24_history_window_secs = 6 * 3600
        self._h24_reversal_threshold = 0.25
        self._h24_reversal_min_samples = 3
        # Only treat a drop-from-peak as "decay" if the peak itself was notable.
        # Without this, established memecoins with normal h24 volatility (MAGA
        # peak=16%, BULL peak=14%) get blocked when their daily cycle dips to
        # zero — even though they're not dying, just cycling.  Set so the filter
        # only fires on tokens that genuinely pumped (peak >= +100%).
        self._h24_reversal_min_peak = 100.0
        self._h24_history_path = os.path.join(
            os.environ.get("DATA_DIR", "/data"), "h24_history.json"
        )
        self._h24_history_dirty = False
        self._load_h24_history()
        # Tier 3: optional AxiomPriceFeed for sub-minute tick buffer reads.
        # Set externally via `dip_scanner.axiom_price_feed = axiom.price_feed`.
        # If present, we pre-subscribe candidates that pass core filters and
        # read tick stats at signal-fire time. None → tier-3 features absent.
        self.axiom_price_feed = None
        # Liquidity-flow stateful tracker — records per-token liquidity over
        # last 1h, computes 5m/15m/60m deltas at signal-fire time. Memecoin-
        # specific: LP adds = team support, LP removes = soft-rug warning.
        from feeds.liquidity_flow import LiquidityFlowTracker
        self._lp_flow = LiquidityFlowTracker(window_secs=3600)

    async def run(self):
        logger.info("[DipScanner] Starting — targeting $1M+ mcap dip entries")
        while True:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"[DipScanner] Scan cycle error: {e}")
            await asyncio.sleep(_SCAN_INTERVAL)

    async def _fetch_btc_klines(self) -> list:
        """
        Fetch recent 1h BTC klines from Kraken public API. Returns a list of
        rows where row[4] is the close price (matches the Binance schema we
        used previously, so callers don't change). Cached 60s. Fail-soft.

        Migrated from Binance 2026-05-03: Railway egress IPs are geo-blocked
        by Binance (silent failure → btc_pc_h1/h4 missing on every entry_meta
        in baseline-mode audit). Kraken is US-based, public, no auth, no
        rate-limit concerns for our 1/min poll. We slice last 5 bars to
        match the prior code's len-5 expectation.
        """
        now = time.monotonic()
        cached_ts, cached = self._btc_cache
        if cached and (now - cached_ts) < 60:
            return cached
        url = "https://api.kraken.com/0/public/OHLC?pair=XBTUSDT&interval=60"
        try:
            import aiohttp as _aio
            async with _aio.ClientSession() as session:
                async with session.get(url, timeout=_aio.ClientTimeout(total=6)) as resp:
                    if resp.status != 200:
                        return cached
                    data = await resp.json()
                    if not isinstance(data, dict) or data.get("error"):
                        return cached
                    result = data.get("result") or {}
                    # Kraken returns the pair under whichever key matches —
                    # often "XBTUSDT" but sometimes the pair name is mapped.
                    bars = None
                    for k, v in result.items():
                        if k == "last":
                            continue
                        if isinstance(v, list):
                            bars = v
                            break
                    if not bars or len(bars) < 5:
                        return cached
                    # Take last 5 bars to match the old Binance limit=5 shape.
                    last5 = bars[-5:]
                    self._btc_cache = (now, last5)
                    return last5
        except Exception:
            pass
        return cached

    async def _fetch_meme_sector(self) -> Optional[dict]:
        """
        Memecoin sector breadth via CoinGecko categories endpoint. Returns
        a dict with category id, market_cap_change_24h (pct), and 24h
        volume — or None on error. Cached 60s.

        Selection: prefer "solana-meme-coins" (most relevant to our trades)
        if present, otherwise fall back to generic "meme-token". Both come
        from the same /coins/categories response (one API call covers all).
        Free tier, no key required, ~10 req/min limit — at 60s cache we're
        well within budget.
        """
        now = time.monotonic()
        cached_ts, cached = self._meme_cache
        if cached is not None and (now - cached_ts) < 60:
            return cached
        url = "https://api.coingecko.com/api/v3/coins/categories"
        try:
            import aiohttp as _aio
            async with _aio.ClientSession() as session:
                async with session.get(url, timeout=_aio.ClientTimeout(total=6)) as resp:
                    if resp.status != 200:
                        return cached
                    data = await resp.json()
                    if not isinstance(data, list):
                        return cached
            preferred = None
            fallback = None
            for cat in data:
                cat_id = (cat.get("id") or "").lower()
                if cat_id == "solana-meme-coins":
                    preferred = cat
                    break
                if cat_id in ("meme-token", "memes") and fallback is None:
                    fallback = cat
            chosen = preferred or fallback
            if not chosen:
                return cached
            out = {
                "id": (chosen.get("id") or "").lower(),
                "name": chosen.get("name") or "",
                "market_cap_change_24h": chosen.get("market_cap_change_24h"),
                "volume_24h": chosen.get("volume_24h"),
                "market_cap": chosen.get("market_cap"),
            }
            self._meme_cache = (now, out)
            return out
        except Exception:
            return cached

    async def _scan_cycle(self):
        # Don't scan if already at max concurrent dip positions
        dip_count = sum(
            1 for pos in self.open_positions_ref.values()
            if getattr(pos, "strategy", "") == "dip_buy"
        )
        if dip_count >= self.max_concurrent:
            logger.info(
                f"[DipScanner] Cycle: at max concurrent ({dip_count}) — skipping scan"
            )
            return

        pairs, source_counts = await self._fetch_candidates()
        now_ms = time.time() * 1000

        c: Counter = Counter()
        trend_reversal_blocked: List[str] = []  # token symbols blocked this cycle
        signals = 0
        for pair in pairs:
            c["fetched"] += 1
            token_address = (pair.get("baseToken") or {}).get("address", "")
            token_symbol = (pair.get("baseToken") or {}).get("symbol", "?")

            if not token_address:
                c["no_addr"] += 1
                continue
            # Case-insensitive match — open_positions can be keyed with either
            # case depending on which feed surfaced the position. Mirror the
            # trader's lowercase check (core/trader.py).
            _addr_lower = token_address.lower()
            if _addr_lower in self.open_positions_ref or token_address in self.open_positions_ref:
                c["already_open"] += 1
                continue
            # Per-token loss cooldown — block rebuy for 30min after a losing
            # dip_buy close on the same token.  Same-token rebuy-after-loss
            # historically nets ~$0 (n=161 across history) but causes acute
            # bleed when a token enters a downtrend (e.g. mexicanunc 4-stop
            # cycle today).  30-min window saves ~$267 today and only ~$41
            # of MAGA's ladder-up wins lifetime.
            if hasattr(self.trader, "is_dip_in_cooldown") and \
                    self.trader.is_dip_in_cooldown(token_address, 1800):
                c["loss_cooldown"] += 1
                continue

            mcap = pair.get("marketCap") or 0
            if mcap < self.min_mcap:
                c["mcap_low"] += 1
                continue
            if mcap > self.max_mcap:
                c["mcap_high"] += 1
                continue

            created_ms = pair.get("pairCreatedAt") or 0
            if created_ms <= 0 or (now_ms - created_ms) < self.min_age_ms:
                c["age"] += 1
                continue

            vol_h24 = (pair.get("volume") or {}).get("h24", 0) or 0
            if vol_h24 < self.min_volume_h24:
                c["vol"] += 1
                continue

            # Turnover filter: require vol_h24 / liquidity >= threshold. Blocks
            # over-liquid tokens where trades don't move price (pippin 0.9×,
            # TROLL 0.5×, 67 1.3×). All known winners are ≥3.9×.
            #
            # GT-only pairs (no DexScreener enrichment) report
            # `reserve_in_usd` which overstates tradeable depth on Meteora
            # DLMM (inactive bins counted as liquidity). Apply a 0.5 discount
            # for those so turnover math matches DS-style depth and we don't
            # buy into a pool that looks deep but slips hard. Pair source is
            # "geckoterminal" when GT is the only data source; DS-enriched
            # pairs overwrite that string.
            liq_usd = float((pair.get("liquidity") or {}).get("usd") or 0)
            if pair.get("_source") == "geckoterminal" and liq_usd > 0:
                liq_usd = liq_usd * 0.5
            turnover = (vol_h24 / liq_usd) if liq_usd > 0 else 0.0
            if liq_usd > 0 and turnover < self.min_turnover_h24:
                c["low_turnover"] += 1
                if not self.baseline_mode:
                    continue

            # Volume-decay filter: require recent-hour volume to be at least
            # min_vol_h1_ratio of the 6h average hourly rate. Blocks tokens
            # whose liquidity is fading mid-trade. Using vol_h6/6 instead of
            # vol_h24/24 because the h24 average is distorted upward by a
            # single recent pump-hour, causing false rejects on healthy
            # post-pump tokens. h6 window smooths over the pump while still
            # catching true decay.
            vol_h1 = (pair.get("volume") or {}).get("h1", 0) or 0
            vol_h6 = (pair.get("volume") or {}).get("h6", 0) or 0
            vol_m5 = (pair.get("volume") or {}).get("m5", 0) or 0
            if self.require_vol_m5 and vol_m5 <= 0:
                c["vol_m5_zero"] += 1
                continue
            # Prefer h6 baseline; fall back to h24 if h6 missing (some GT
            # pairs lack the h6 volume key).
            if vol_h6 > 0:
                vol_baseline_per_hour = vol_h6 / 6.0
            else:
                vol_baseline_per_hour = vol_h24 / 24.0
            if vol_h1 < vol_baseline_per_hour * self.min_vol_h1_ratio:
                c["vol_h1_decay"] += 1
                if not self.baseline_mode:
                    continue

            pc_h24 = (pair.get("priceChange") or {}).get("h24", 0) or 0
            pc_h6 = (pair.get("priceChange") or {}).get("h6", 0) or 0
            pc_h1 = (pair.get("priceChange") or {}).get("h1", 0) or 0
            pc_m5 = (pair.get("priceChange") or {}).get("m5", 0) or 0

            if pc_h24 <= 0:
                c["red_h24"] += 1
                if not self.baseline_mode:
                    continue

            # Track h24/h1/h6 history for trend-reversal detection AND
            # pre-entry trajectory features. Append each cycle (only after the
            # red_h24 gate, so negative readings don't corrupt the peak) and
            # prune entries older than the 6h window. Wall-clock time so
            # history survives process restarts.
            #
            # Entry shape: (ts, pc_h24, pc_h1, pc_h6). Legacy entries loaded
            # from /data/h24_history.json may have only (ts, pc_h24) — those
            # get padded with None for h1/h6 in _load_h24_history. Readers
            # downstream of the append (peak detection, trajectory derivation)
            # tolerate None entries by skipping them.
            addr_lower = token_address.lower()
            hist = self._h24_history.setdefault(addr_lower, deque())
            wall_now = time.time()
            hist.append((wall_now, pc_h24, pc_h1, pc_h6))
            while hist and (wall_now - hist[0][0]) > self._h24_history_window_secs:
                hist.popleft()
            self._h24_history_dirty = True

            # Top-exhaustion filter: token pumped +50% to +200% over the last
            # 6h AND is still pumping (h1 >= +5%). The "small dip on already-
            # extended uptrend" pattern — the worst single bucket on 04-27
            # (-$351 net across 10 trades, lifetime -$334). Uses pc_h6
            # directly (the actual 6h price change), NOT peak_h24 (which is
            # the max of the 24h-anchor history and represents something else
            # entirely). Doesn't need history samples — pure snapshot filter.
            if 50.0 <= pc_h6 <= 200.0 and pc_h1 >= 5.0:
                c["top_exhaustion"] += 1
                if not self.baseline_mode:
                    continue

            # Trend-reversal filter: reject if current h24 has collapsed to
            # <25% of recent peak across last 6h of observations AND price is
            # actually declining on 6h (pc_h6 <= 0). The h6 guard prevents
            # the anchor-slide false positive: a newly-pumped token like SCAM
            # (peaked at +39721% h24, now +629%) looks decayed by ratio but
            # is still uptrending on 6h. Catches mexicanunc / ASTEROID class
            # true decay (h24 anchor falling AND h6 negative).
            if len(hist) >= self._h24_reversal_min_samples:
                peak_h24 = max(entry[1] for entry in hist)
                if peak_h24 >= self._h24_reversal_min_peak \
                        and (pc_h24 / peak_h24) < self._h24_reversal_threshold \
                        and pc_h6 <= 0:
                    c["trend_reversal"] += 1
                    if len(trend_reversal_blocked) < 6:  # cap log noise
                        trend_reversal_blocked.append(
                            f"{token_symbol}({pc_h24:.0f}%/peak{peak_h24:.0f}%/h6{pc_h6:+.0f}%)"
                        )
                    if not self.baseline_mode:
                        continue
            if pc_h1 >= 0 and pc_m5 >= 0:
                c["no_dip"] += 1
                if not self.baseline_mode:
                    continue

            # Mid-dip filter: h1 in [-6%, -5%) is the band where data shows
            # clear -EV (n=3 lifetime, 1/3 wins, -$134 net, -$44.61/trade —
            # ASTEROID/BOAR-class). Original [-10%, -5%) range was too broad:
            # 04-27 diagnostic showed [-10, -7) had 23/25 wins (92%, +$394
            # net) — the MAGA/BULL/WIFE/DUMBMONEY/SAM archetype dip zone.
            # Narrowed to [-6, -5) to preserve those winners while still
            # blocking the shallow-dip zone where losses concentrate.
            if -6.0 <= pc_h1 < -5.0:
                c["h1_mid_dip"] += 1
                if not self.baseline_mode:
                    continue

            # Dip-already-over filter: m5 has turned positive but hasn't built
            # momentum yet ([0%, +3%) band). Historically -EV: n=43, 42% WR,
            # -$50 net. Buying the bounce-top after the dip ended but before
            # the move resumes — top-tick zone. Other m5 buckets are +EV
            # (deep dip, active dip, dip-ending all >50% WR; bouncing/running
            # buckets >75% WR).
            if 0 <= pc_m5 < 3.0:
                c["m5_dip_over"] += 1
                if not self.baseline_mode:
                    continue

            # Falling-knife filter: block if m5 is sharply negative while h1 is
            # weakly positive [0%, +5%).  Original rule (m5<-5% AND h1>0) was
            # too broad: 2D backtest showed h1 >= +5% with m5 < -5% is the
            # "uptrend pullback" pattern, n=40 historical trades worth +$290
            # (MAGA 80% wr +$127, WIFE 71% wr +$51, DUMBMONEY 2/2 +$54).
            # Loosened to only [0, +5%): preserves the original mexicanunc
            # protection (h1=+3.4% m5=-9.9% case) while no longer cutting
            # uptrend-pullback winners. Net lifetime: +$369 vs old rule.
            if pc_m5 < -5.0 and 0 < pc_h1 < 5.0:
                c["falling_knife"] += 1
                if not self.baseline_mode:
                    continue

            # Mega-pump middle-band filter: on tokens with a recent extreme
            # pump (pc_h24 > +5000% OR pc_h6 > +200%), dip entries cluster
            # into three archetypes by h1:
            #   h1 <= -15%: deep pullback — real bounce setup, wins
            #   h1 >= +50%: raging continuation — wins (ride the pump)
            #   h1 between -15 and +50 with m5<0: "dead middle" — dies out
            # Use OR with pc_h6 because pc_h24 anchor slides — a token that
            # genuinely pumped +40000% will eventually show pc_h24 < 5000%
            # as the 24h window moves past the peak, making the original
            # filter ineffective on day-old mega-pumps. pc_h6 > 200% catches
            # the same regime in a window that doesn't decay on day-old pumps.
            mega_pump = pc_h24 > 5000.0 or pc_h6 > 200.0
            if (mega_pump
                    and pc_m5 < 0
                    and -15.0 <= pc_h1 <= 50.0):
                c["mega_pump_middle"] += 1
                if not self.baseline_mode:
                    continue

            # Order-flow filter: require h6 buy/sell txn ratio >= threshold.
            # Reject tokens without txns data (prev bug: GT-sourced pairs had
            # no txns field and bypassed this check — 67/TROLL/pippin all
            # slipped through. After enrichment we expect every pair to have
            # txns, so missing data now means the token is too obscure.)
            txns_h6 = (pair.get("txns") or {}).get("h6") or {}
            b_h6 = int(txns_h6.get("buys") or 0)
            s_h6 = int(txns_h6.get("sells") or 0)
            if b_h6 == 0 and s_h6 == 0:
                c["bs_h6_missing"] += 1
                if not self.baseline_mode:
                    continue
            ratio_h6 = (b_h6 / s_h6) if s_h6 > 0 else float("inf")
            if ratio_h6 < self.min_txn_ratio_h6:
                c["bs_h6"] += 1
                self._rejected_distribution += 1
                if not self.baseline_mode:
                    continue

            # bs_m5 — current-moment order flow. Logged but not filtered yet;
            # gathering 24-48h of data to test whether it separates wins/losses
            # at the entry snapshot level (the snapshot-level pattern bs_h6
            # can't see). Zero when no m5 txns yet.
            txns_m5 = (pair.get("txns") or {}).get("m5") or {}
            b_m5 = int(txns_m5.get("buys") or 0)
            s_m5 = int(txns_m5.get("sells") or 0)
            if s_m5 > 0:
                ratio_m5 = b_m5 / s_m5
            elif b_m5 > 0:
                ratio_m5 = float("inf")
            else:
                ratio_m5 = 0.0

            # bs_h1 — recent-hour order flow. Logged for analysis only; once
            # 3-7 days of trades carry it in the reason string, backtest
            # whether it separates wins from losses (esp. divergence cases:
            # bs_h6 strong but bs_h1 weak = accumulation-ending; or weak h6
            # but rising h1 = early-entry signal).
            txns_h1 = (pair.get("txns") or {}).get("h1") or {}
            b_h1 = int(txns_h1.get("buys") or 0)
            s_h1 = int(txns_h1.get("sells") or 0)
            if s_h1 > 0:
                ratio_h1 = b_h1 / s_h1
            elif b_h1 > 0:
                ratio_h1 = float("inf")
            else:
                ratio_h1 = 0.0

            # Seller-dominance filter: bs_h1 < 0.85 means sells outnumber buys
            # over the last hour by >18%.  Combined with m5 < 0 (price still
            # ticking down), this is "sellers winning + price falling" —
            # not a dip to buy, an active distribution.  Lifetime impact:
            # blocks 10 trades for net +$287, today saves +$186.  Skip on
            # truly missing data (b=0 AND s=0) but include pure-sell bars
            # (b=0, s>0 → ratio=0.0) — those are maximally bearish, not "no
            # data". Old `0 < ratio < 0.85` guard wrongly let them pass.
            if (b_h1 > 0 or s_h1 > 0) and ratio_h1 < 0.85 and pc_m5 < 0:
                c["seller_h1_red_m5"] += 1
                if not self.baseline_mode:
                    continue

            # Pumped + sellers cooling filter: token pumped over the hour
            # (h1 > +3%) but the most-recent 5min has m5 sells dominating
            # (bs_m5 < 1) AND price hasn't pulled back meaningfully (m5 > -2%).
            # Pattern: "pumped, now stalling at top with sellers winning the
            # moment" — SPIKE-class top-buy.  Lifetime: blocks 8 trades,
            # saves +$203; today saves +$150.  Same pure-sell-bar fix as
            # seller_h1_red_m5 above.
            if (pc_h1 > 3.0 and pc_m5 > -2.0
                    and (b_m5 > 0 or s_m5 > 0)
                    and ratio_m5 < 1.0):
                c["seller_pump"] += 1
                if not self.baseline_mode:
                    continue

            dip_count = sum(
                1 for pos in self.open_positions_ref.values()
                if getattr(pos, "strategy", "") == "dip_buy"
            )
            if dip_count >= self.max_concurrent:
                c["cap_reached"] += 1
                break

            # ── Final pre-buy gate: 1m candle reversal confirmation ──
            # All other filters passed.  Fetch last 5 × 1m candles for the
            # pool and require ≥1 green close in the last 3 minutes.  Catches
            # "dip already reversed and now distributing" patterns that m5
            # smooths out.  Fail-open on fetch errors (don't block buys when
            # GeckoTerminal API is down).  Cost: 1 GT call per shortlisted
            # candidate (~1-2 per cycle), well within the 25/min budget.
            pair_addr_for_1m = pair.get("pairAddress", "") or ""
            # ── Phase 0: single multi-timeframe fetch ──
            # One assemble_chart_data call powers m1_features, range_features,
            # vwap_features, AND chart_reader. Replaces the prior three
            # separate fetch_1m/5m/15m calls in this loop, which all
            # competed for the same GT rate budget and dropped to ~25%
            # coverage on structural fields under baseline-mode load.
            # The chart_reader call below reuses this data via the
            # `chart_data=` keyword arg — zero extra GT calls.
            from feeds.chart_data import assemble_chart_data as _assemble
            _chart_data = None
            if pair_addr_for_1m:
                try:
                    _chart_data = await _assemble(self.gt_client, pair_addr_for_1m)
                except Exception as _e:
                    logger.debug(f"[DipScanner] chart_data assemble error for {token_symbol}: {_e}")
            m1_features: dict = {}
            if pair_addr_for_1m:
                # Slice to last 5 × 1m candles to preserve original m1 feature
                # semantics (5-candle window for m1 gates).
                cs = (_chart_data.candles_1m[-5:] if _chart_data and _chart_data.candles_1m else [])
                if cs and len(cs) >= 3:
                    last3 = cs[-3:]
                    green_in_last3 = sum(1 for k in last3 if k.close > k.open)
                    last_close_pct = (
                        (last3[-1].close / last3[-1].open - 1) * 100
                        if last3[-1].open > 0 else 0.0
                    )
                    cum_3min_pct = (
                        (last3[-1].close / last3[0].open - 1) * 100
                        if last3[0].open > 0 else 0.0
                    )
                    # Volume spike: most-recent 1m vol vs avg of prior candles
                    prior_vols = [k.volume for k in cs[:-1]]
                    avg_prior_vol = sum(prior_vols) / len(prior_vols) if prior_vols else 0.0
                    vol_spike_ratio = (
                        last3[-1].volume / avg_prior_vol if avg_prior_vol > 0 else 0.0
                    )
                    # Tier 1 derivations from the same 5 × 1m candles
                    red_count_5 = sum(1 for k in cs if k.close < k.open)
                    # Consecutive-red streak at end of sequence
                    consec_red = 0
                    for k in reversed(cs):
                        if k.close < k.open:
                            consec_red += 1
                        else:
                            break
                    # Avg body size as % of close price (volatility proxy)
                    bodies = [abs(k.close - k.open) / k.close for k in cs if k.close > 0]
                    body_pct_avg = (sum(bodies) / len(bodies) * 100) if bodies else 0.0
                    # Largest 1m drop (low/open - 1) of any candle in last 5
                    drops = [(k.low / k.open - 1) * 100 for k in cs if k.open > 0]
                    max_drop_1m = min(drops) if drops else 0.0
                    # Where in its own range did the most recent 1m close?
                    last = cs[-1]
                    last_rng = last.high - last.low
                    close_in_range = (
                        (last.close - last.low) / last_rng if last_rng > 0 else 0.5
                    )
                    # Higher-highs vs lower-highs sequence (count transitions)
                    higher_highs = lower_highs = 0
                    for i in range(1, len(cs)):
                        if cs[i].high > cs[i-1].high:
                            higher_highs += 1
                        elif cs[i].high < cs[i-1].high:
                            lower_highs += 1
                    m1_features = {
                        "1m_green_in_last3": green_in_last3,
                        "1m_last_close_pct": round(last_close_pct, 3),
                        "1m_cum_3min_pct":   round(cum_3min_pct, 3),
                        "1m_volume_spike":   round(vol_spike_ratio, 3),
                        "1m_candle_count":   len(cs),
                        "1m_red_count_5":    red_count_5,
                        "1m_consec_red":     consec_red,
                        "1m_body_pct_avg":   round(body_pct_avg, 3),
                        "1m_max_drop":       round(max_drop_1m, 3),
                        "1m_close_in_range": round(close_in_range, 3),
                        "1m_higher_highs":   higher_highs,
                        "1m_lower_highs":    lower_highs,
                    }
                    if green_in_last3 == 0:
                        c["no_1m_reversal"] += 1
                        logger.info(
                            f"[DipScanner] 1m gate: {token_symbol} — "
                            f"no green close in last 3 min "
                            f"(cum_3min={cum_3min_pct:+.1f}%) — skipping"
                        )
                        if not self.baseline_mode:
                            continue

                    # m1 top-tick filter: most recent 1m candle closed >=+2%
                    # green.  Counter-intuitive — a really green last candle
                    # is the LAST burst of buying before reversal, not a
                    # confirmed reversal.  Lifetime: blocks 7 trades, saves
                    # +$366; today saved +$299 (4 trades, 0% win rate).
                    if last_close_pct >= 2.0:
                        c["m1_top_tick"] += 1
                        logger.info(
                            f"[DipScanner] m1_top_tick: {token_symbol} — "
                            f"last 1m close {last_close_pct:+.2f}% — skipping"
                        )
                        if not self.baseline_mode:
                            continue

                    # m1 false-bounce filter: cumulative 3-min change in
                    # [+1%, +3%] band is the "dip is barely over but momentum
                    # hasn't built" zone — buying right at the top tick of
                    # the bounce.  Lifetime: blocks 18 trades, saves +$245;
                    # today saved +$357 (7 trades, 14% win rate).
                    if 1.0 <= cum_3min_pct < 3.0:
                        c["m1_false_bounce"] += 1
                        logger.info(
                            f"[DipScanner] m1_false_bounce: {token_symbol} — "
                            f"cum_3min={cum_3min_pct:+.2f}% — skipping"
                        )
                        if not self.baseline_mode:
                            continue

                    # Top-consolidation filter: h1 pumped (>+3%) AND 1m
                    # cumulative is near zero (|cum_3m| < 0.5%) — token has
                    # plateaued at the top of recent range with no momentum
                    # in either direction.  Range-position proxy for "stuck
                    # at the top of the chart."  Lifetime: blocks 10 trades,
                    # saves +$402; today saves +$295.
                    if pc_h1 > 3.0 and abs(cum_3min_pct) < 0.5:
                        c["top_consolidation"] += 1
                        logger.info(
                            f"[DipScanner] top_consolidation: {token_symbol} — "
                            f"h1={pc_h1:+.1f}% but cum_3m={cum_3min_pct:+.2f}% "
                            f"(stuck at top) — skipping"
                        )
                        if not self.baseline_mode:
                            continue

            # ── Range-position capture (5m candle stack) ──
            # Fetch last 12 × 5m candles (= 1h coverage) to compute where
            # the current price sits in the 1h range.  Stored in entry_meta
            # for future backtesting; not yet used as a filter (gathering
            # data first).  Fail-open on fetch errors.
            range_features: dict = {}
            if pair_addr_for_1m:
                # Use last 12 × 5m candles (1h window) from the shared
                # chart_data — preserves range_features semantics exactly
                # (high_1h, pct_in_1h_range, 5m_lower_highs, vol_decay all
                # assume a 12-candle context). chart_data fetches 144
                # (12h) so chart_reader gets full S/R history while
                # dip_scanner's 1h-window features stay 1h.
                cs5 = (_chart_data.candles_5m[-12:] if _chart_data and _chart_data.candles_5m else [])
                if cs5 and len(cs5) >= 4:
                    high_1h = max(k.high for k in cs5)
                    low_1h = min(k.low for k in cs5)
                    cur_price = cs5[-1].close
                    rng_1h = high_1h - low_1h
                    pct_in_1h_range = (
                        (cur_price - low_1h) / rng_1h if rng_1h > 0 else 0.5
                    )
                    last_5m = cs5[-1]
                    last_5m_rng = last_5m.high - last_5m.low
                    pct_in_5m_range = (
                        (cur_price - last_5m.low) / last_5m_rng if last_5m_rng > 0 else 0.5
                    )
                    # Tier 1: red-count, consecutive-red/green streak, lower-highs
                    # count, and volume-decay across 12 × 5m candles (covers 1h).
                    red_count_5m = sum(1 for k in cs5 if k.close < k.open)
                    consec_red_5m = 0
                    for k in reversed(cs5):
                        if k.close < k.open:
                            consec_red_5m += 1
                        else:
                            break
                    consec_green_5m = 0
                    for k in reversed(cs5):
                        if k.close > k.open:
                            consec_green_5m += 1
                        else:
                            break
                    # Lower-highs count: distribution pattern — each 5m high
                    # below the prior 5m high. BELKA showed exactly this pattern.
                    lower_highs_5m = sum(
                        1 for i in range(1, len(cs5)) if cs5[i].high < cs5[i-1].high
                    )
                    # Volume decay: avg vol of last 3 × 5m vs first 3 × 5m
                    if len(cs5) >= 6:
                        first_avg = sum(k.volume for k in cs5[:3]) / 3
                        last_avg = sum(k.volume for k in cs5[-3:]) / 3
                        vol_decay = (last_avg / first_avg) if first_avg > 0 else 0.0
                    else:
                        vol_decay = 1.0

                    # Token-level 1H EMA slope (Layer 2 of multi-layer trend
                    # system). Smooths the 5m closes so a single big spike
                    # doesn't dominate. Slope evaluated over 30 min (current
                    # EMA vs 6 candles ago) — long enough that a single 5m
                    # dip doesn't flip the verdict. span=6 → α = 2/7 ≈ 0.286,
                    # ~30 min smoothing window.
                    #
                    # IMPORTANT: a healthy "dip in uptrend" naturally has a
                    # mildly negative local EMA slope (the dip drags it).
                    # Thresholds are LOOSE — only flag truly rolling-over
                    # tokens, not natural dip-buy candidates. Will tune from
                    # forward data.
                    _ema_span = 6
                    _alpha = 2.0 / (_ema_span + 1)
                    _ema_series: list = []
                    for _k in cs5:
                        if not _ema_series:
                            _ema_series.append(_k.close)
                        else:
                            _ema_series.append(_alpha * _k.close + (1 - _alpha) * _ema_series[-1])
                    token_ema_slope_pct = None
                    token_ema_verdict = "?"
                    if len(_ema_series) >= 7 and _ema_series[-7] > 0:
                        _slope_pct = (_ema_series[-1] / _ema_series[-7] - 1) * 100
                        token_ema_slope_pct = round(_slope_pct, 3)
                        # Shadow thresholds (starting points; tune from data):
                        # -1.5% over 30 min = ~3% over 1h — that's a real
                        # rollover, not a dip. +0.3% over 30 min = uptrending.
                        if _slope_pct < -1.5:
                            token_ema_verdict = "BLOCK"
                        elif _slope_pct > 0.3:
                            token_ema_verdict = "PASS"
                        else:
                            token_ema_verdict = "NEUTRAL"

                    # Volume during dip — accelerating vs dying. Walk back
                    # from the latest candle; the "dip leg" is the trailing
                    # consecutive red candles, and the "pre-dip leg" is the
                    # green/doji candles immediately before. If no run-up
                    # before the dip, fall back to comparing the dip leg vs
                    # the rest of the window. Ratio > 1.0 = active distribution
                    # (volume rising into the dip = bad). Ratio < 1.0 = dying
                    # volume / exhaustion (more likely a real bounce setup).
                    dip_volume_ratio = None
                    dip_volume_verdict = "?"
                    dip_leg_candles = 0
                    _dip_leg = []
                    for _k in reversed(cs5):
                        if _k.close < _k.open:
                            _dip_leg.insert(0, _k)
                        else:
                            break
                    dip_leg_candles = len(_dip_leg)
                    if _dip_leg:
                        _dip_start_idx = len(cs5) - len(_dip_leg)
                        _run_leg = []
                        for _i in range(_dip_start_idx - 1, -1, -1):
                            _k = cs5[_i]
                            if _k.close >= _k.open:
                                _run_leg.insert(0, _k)
                            else:
                                break
                        if not _run_leg:
                            # No run-up before the dip — use prior window as baseline
                            _run_leg = cs5[:_dip_start_idx]
                        if _run_leg and len(_dip_leg) >= 1:
                            _dip_vol = sum(k.volume for k in _dip_leg) / len(_dip_leg)
                            _run_vol = sum(k.volume for k in _run_leg) / len(_run_leg)
                            if _run_vol > 0:
                                dip_volume_ratio = round(_dip_vol / _run_vol, 3)
                                # Shadow thresholds (starting points):
                                if dip_volume_ratio >= 1.5:
                                    dip_volume_verdict = "BLOCK"  # active distribution
                                elif dip_volume_ratio <= 0.7:
                                    dip_volume_verdict = "PASS"   # exhaustion
                                else:
                                    dip_volume_verdict = "NEUTRAL"

                    # Layer 1: HH/HL structure detection via fractal swings.
                    # A fractal is a candle whose high (or low) is greater
                    # (or less) than the N candles on both sides. With N=2
                    # over our 12-candle window, swings can occur at indices
                    # 2..9. With memecoin price action this typically yields
                    # 1-3 swing highs and 1-3 swing lows.
                    #
                    # Pattern classification (per claude-ideas Part 4 Layer 1):
                    #   - all swing highs ascending AND all swing lows
                    #     ascending → uptrend (HH + HL)
                    #   - all swing highs descending AND all swing lows
                    #     descending → downtrend (LH + LL)
                    #   - else → mixed / no clear trend
                    _N = 2
                    _swing_highs = []
                    _swing_lows = []
                    for _i in range(_N, len(cs5) - _N):
                        _h_i = cs5[_i].high
                        _l_i = cs5[_i].low
                        _is_sh = (
                            all(cs5[_i].high > cs5[_j].high for _j in range(_i - _N, _i))
                            and all(cs5[_i].high > cs5[_j].high for _j in range(_i + 1, _i + _N + 1))
                        )
                        _is_sl = (
                            all(cs5[_i].low < cs5[_j].low for _j in range(_i - _N, _i))
                            and all(cs5[_i].low < cs5[_j].low for _j in range(_i + 1, _i + _N + 1))
                        )
                        if _is_sh:
                            _swing_highs.append((_i, _h_i))
                        if _is_sl:
                            _swing_lows.append((_i, _l_i))
                    structure_pattern = "insufficient"
                    structure_verdict = "?"
                    if len(_swing_highs) >= 2 and len(_swing_lows) >= 2:
                        _sh = _swing_highs[-3:]
                        _sl = _swing_lows[-3:]
                        _all_sh_up = all(_sh[_i][1] > _sh[_i - 1][1] for _i in range(1, len(_sh)))
                        _all_sl_up = all(_sl[_i][1] > _sl[_i - 1][1] for _i in range(1, len(_sl)))
                        _all_sh_down = all(_sh[_i][1] < _sh[_i - 1][1] for _i in range(1, len(_sh)))
                        _all_sl_down = all(_sl[_i][1] < _sl[_i - 1][1] for _i in range(1, len(_sl)))
                        if _all_sh_up and _all_sl_up:
                            structure_pattern = "uptrend"
                            structure_verdict = "PASS"
                        elif _all_sh_down and _all_sl_down:
                            structure_pattern = "downtrend"
                            structure_verdict = "BLOCK"
                        else:
                            structure_pattern = "mixed"
                            structure_verdict = "NEUTRAL"

                    range_features = {
                        "1h_high": round(high_1h, 8),
                        "1h_low": round(low_1h, 8),
                        "5m_candle_count": len(cs5),
                        "pct_in_1h_range": round(pct_in_1h_range, 3),
                        "5m_high": round(last_5m.high, 8),
                        "5m_low": round(last_5m.low, 8),
                        "pct_in_5m_range": round(pct_in_5m_range, 3),
                        "5m_red_count": red_count_5m,
                        "5m_consec_red": consec_red_5m,
                        "5m_consec_green": consec_green_5m,
                        "5m_lower_highs": lower_highs_5m,
                        "5m_vol_decay": round(vol_decay, 3),
                        # Layer 1: HH/HL structure (fractal swings) — shadow.
                        "structure_swing_highs": len(_swing_highs),
                        "structure_swing_lows": len(_swing_lows),
                        "structure_pattern": structure_pattern,
                        "structure_verdict": structure_verdict,
                        # Layer 2 trend (1H EMA slope) — shadow, no enforcement.
                        "token_ema_slope_pct": token_ema_slope_pct,
                        "token_ema_verdict": token_ema_verdict,
                        # Volume-during-dip — shadow, no enforcement.
                        "dip_volume_ratio": dip_volume_ratio,
                        "dip_volume_verdict": dip_volume_verdict,
                        "dip_leg_candles": dip_leg_candles,
                    }

            # ── Anchored VWAP (Layer 3 of multi-layer trend system) ──
            # 24h volume-weighted average price computed from 15m candles.
            # Tokens younger than 24h: GT returns whatever exists since launch
            # (effectively "anchored from launch"). Tokens older: anchored
            # from 24h ago (recent daily cycle). Either way: price > VWAP =
            # aggregate buyer base in profit; dip-buying is real. Price <<
            # VWAP = aggregate underwater; dips get sold into.
            #
            # Logged-only at proposed thresholds; will tune from forward data.
            vwap_features: dict = {}
            if pair_addr_for_1m:
                # 24h anchored VWAP from full 15m series (chart_data fetches
                # 96 = 24h coverage). Use as-is — same window as before.
                cs15 = (_chart_data.candles_15m if _chart_data and _chart_data.candles_15m else [])
                if cs15 and len(cs15) >= 4:
                    _num = 0.0
                    _den = 0.0
                    for _k in cs15:
                        _typ = (_k.high + _k.low + _k.close) / 3.0
                        _num += _typ * _k.volume
                        _den += _k.volume
                    if _den > 0:
                        _vwap = _num / _den
                        _cur = cs15[-1].close
                        if _vwap > 0:
                            _pct_above = (_cur / _vwap - 1) * 100
                            vwap_features["vwap_h24_usd"] = round(_vwap, 8)
                            vwap_features["pct_above_vwap_h24"] = round(_pct_above, 2)
                            vwap_features["vwap_h24_candles"] = len(cs15)
                            # Shadow thresholds (starting points; tune from
                            # data). The "underwater" zone is where holders
                            # sell into bounces; the "in profit" zone is
                            # where dips get bought.
                            if _pct_above < -10:
                                vwap_features["vwap_h24_verdict"] = "BLOCK"  # holders deeply underwater
                            elif _pct_above > 0:
                                vwap_features["vwap_h24_verdict"] = "PASS"   # holders in profit
                            else:
                                vwap_features["vwap_h24_verdict"] = "NEUTRAL"
                    # 24h realized volatility — stdev of 15m log-returns,
                    # annualized to a per-h24 percentage. Input for any
                    # future vol-adjusted sizing; for now logged-only.
                    try:
                        import math as _math
                        _rets = []
                        for _i in range(1, len(cs15)):
                            _p0 = cs15[_i - 1].close
                            _p1 = cs15[_i].close
                            if _p0 > 0 and _p1 > 0:
                                _rets.append(_math.log(_p1 / _p0))
                        if len(_rets) >= 4:
                            _mean = sum(_rets) / len(_rets)
                            _var = sum((r - _mean) ** 2 for r in _rets) / (len(_rets) - 1)
                            _stdev = _math.sqrt(_var)
                            # Stdev per 15m bar → scale to 24h window (96 bars).
                            _h24_stdev = _stdev * _math.sqrt(96)
                            vwap_features["token_volatility_h24_pct"] = round(_h24_stdev * 100, 3)
                            vwap_features["token_volatility_samples"] = len(_rets)
                    except Exception:
                        pass

            # ── Multi-layer trend score (combiner) ──
            # Aggregates the per-layer verdicts into a single normalized
            # score in [-1, +1]. Layers that didn't produce a verdict (e.g.
            # candle fetch failed, regime data missing) are skipped — the
            # normalization divides by the number of layers that DID
            # contribute, so a partial score is still meaningful.
            #
            # Layer 5 (holder concentration) lives in the trader, not here,
            # so it's omitted from the entry-time score. We have 5 layers
            # available pre-buy: structure, EMA slope, VWAP, dip volume,
            # regime. claude-ideas Part 4 suggests "score >= +3 of 6" as
            # uptrend; with 5 layers and 0/+1/-1 mapping that's normalized
            # +0.5. We start a bit looser (+0.4) and tune from data.
            #
            # Logged-only — not used as a filter (yet). Once forward data
            # validates, can promote to enforce or use as a tiebreaker
            # alongside Filter A.
            # sol_features is computed AFTER this block (in the SOL/BTC fetch
            # below); on the first candidate of a cycle the reference would
            # fire UnboundLocalError. Use locals().get to read the prior
            # iteration's binding when present, empty dict otherwise. The
            # first candidate's L6 verdict is None either way — sol_features
            # is populated for subsequent candidates in the same cycle.
            _sf = locals().get("sol_features") or {}
            _layer_verdicts = {
                "L1_structure": range_features.get("structure_verdict") if range_features else None,
                "L2_ema_slope": range_features.get("token_ema_verdict") if range_features else None,
                "L3_vwap": vwap_features.get("vwap_h24_verdict") if vwap_features else None,
                "L4_dip_volume": range_features.get("dip_volume_verdict") if range_features else None,
                "L6_regime": _sf.get("regime") if _sf else None,
            }
            _trend_score = 0
            _trend_present = 0
            for _v in _layer_verdicts.values():
                if _v in ("PASS", "uptrend", "up"):
                    _trend_score += 1
                    _trend_present += 1
                elif _v in ("BLOCK", "downtrend", "down"):
                    _trend_score -= 1
                    _trend_present += 1
                elif _v in ("NEUTRAL", "mixed", "flat"):
                    _trend_present += 1
            trend_features: dict = {}
            if _trend_present > 0:
                _norm = _trend_score / _trend_present
                trend_features["trend_score_raw"] = _trend_score
                trend_features["trend_score_layers"] = _trend_present
                trend_features["trend_score_norm"] = round(_norm, 3)
                if _norm >= 0.4:
                    trend_features["trend_score_verdict"] = "PASS"
                elif _norm <= -0.4:
                    trend_features["trend_score_verdict"] = "BLOCK"
                else:
                    trend_features["trend_score_verdict"] = "NEUTRAL"
                trend_features["trend_layer_verdicts"] = _layer_verdicts

            c["signal"] += 1
            signals += 1

            # Format bs_m5/bs_h1 as 'inf' when we have buys but zero sells — clearer
            # than a giant float when everyone's buying and nobody's selling.
            bs_m5_str = "inf" if ratio_m5 == float("inf") else f"{ratio_m5:.2f}"
            bs_h1_str = "inf" if ratio_h1 == float("inf") else f"{ratio_h1:.2f}"

            logger.info(
                f"[DipScanner] Signal: {token_symbol} "
                f"mcap=${mcap/1e6:.1f}M | 24h={pc_h24:+.1f}% 1h={pc_h1:+.1f}% 5m={pc_m5:+.1f}% "
                f"vol24h=${vol_h24/1000:.0f}k bs_h6={ratio_h6:.2f} bs_h1={bs_h1_str} bs_m5={bs_m5_str}"
            )

            self._last_buy_time = time.monotonic()
            self.signals_fired += 1

            # ── Tier 2a: SOL + BTC regime context ──
            # Memecoins amplify SOL/BTC moves. Edge-half-life test (2026-05-01)
            # showed WR drops 53% → 39% over 5 days; date-shuffle p=0.016
            # confirms day-level signal is real. SOL/BTC trend is the most
            # likely regime variable behind the bimodal pattern. Single GT
            # call (extended to 4h coverage) + single Binance API call (BTC),
            # cached 60s, fail-open.
            sol_features: dict = {}
            sol_5m = []
            try:
                _SOL_POOL = "83v8iPyZihDEjDdY8RdZddyZNyUtXngz69Lgo9Kt5d6d"  # SOL/USDC Raydium
                # 48 × 5min = 4h coverage. Last 12 (1h), last 48 (4h).
                sol_5m = await self.gt_client.fetch_5m(_SOL_POOL, limit=48)
                sol_1m = await self.gt_client.fetch_1m(_SOL_POOL, limit=5)
                if sol_5m and len(sol_5m) >= 2:
                    sol_pc_m5 = (sol_5m[-1].close / sol_5m[-2].close - 1) * 100 if sol_5m[-2].close > 0 else 0.0
                    sol_features["sol_pc_m5"] = round(sol_pc_m5, 3)
                    # h1 from last 12 candles (60 min)
                    if len(sol_5m) >= 12:
                        h1_anchor = sol_5m[-12].close
                        if h1_anchor > 0:
                            sol_features["sol_pc_h1"] = round((sol_5m[-1].close / h1_anchor - 1) * 100, 3)
                    else:
                        sol_features["sol_pc_h1"] = round((sol_5m[-1].close / sol_5m[0].close - 1) * 100, 3)
                    # h4 from full 48-candle window (240 min)
                    if len(sol_5m) >= 48:
                        h4_anchor = sol_5m[-48].close
                        if h4_anchor > 0:
                            sol_features["sol_pc_h4"] = round((sol_5m[-1].close / h4_anchor - 1) * 100, 3)
                if sol_1m and len(sol_1m) >= 2:
                    sol_pc_m1 = (sol_1m[-1].close / sol_1m[-2].close - 1) * 100 if sol_1m[-2].close > 0 else 0.0
                    sol_pc_3m = (sol_1m[-1].close / sol_1m[0].close - 1) * 100 if sol_1m[0].close > 0 else 0.0
                    sol_features["sol_pc_m1"] = round(sol_pc_m1, 3)
                    sol_features["sol_pc_3m"] = round(sol_pc_3m, 3)
            except Exception as _e:
                logger.debug(f"[DipScanner] SOL fetch error: {_e}")

            # BTC regime — Binance public klines (no key, fast, free).
            # 1h candles × 5 → enough for h1 (last close vs prev) and h4
            # (last close vs 4 candles ago). Cached 60s via _btc_cache.
            try:
                btc_klines = await self._fetch_btc_klines()
                if btc_klines and len(btc_klines) >= 5:
                    last_close = float(btc_klines[-1][4])
                    prev_close = float(btc_klines[-2][4])
                    h4_anchor = float(btc_klines[-5][4])
                    if prev_close > 0:
                        sol_features["btc_pc_h1"] = round((last_close / prev_close - 1) * 100, 3)
                    if h4_anchor > 0:
                        sol_features["btc_pc_h4"] = round((last_close / h4_anchor - 1) * 100, 3)
            except Exception as _e:
                logger.debug(f"[DipScanner] BTC fetch error: {_e}")

            # Memecoin sector breadth — CoinGecko category endpoint.
            # If SOL is up but memes are dumping, dip-buys still die.
            # h24 only at this resolution (CG doesn't expose h1 at category
            # level on the free tier). Stored as analytics field; a future
            # version of the regime tag could fold it in.
            try:
                meme = await self._fetch_meme_sector()
                if meme:
                    _msc = meme.get("market_cap_change_24h")
                    if _msc is not None:
                        sol_features["meme_sector_pct_h24"] = round(float(_msc), 3)
                        sol_features["meme_sector_id"] = meme.get("id") or ""
            except Exception as _e:
                logger.debug(f"[DipScanner] meme-sector fetch error: {_e}")

            # Derive regime tag from sol h1+h4 + btc h1+h4 + meme sector h24.
            # Up if SOL trending and meme sector not dumping. Down if SOL
            # rolling over OR meme sector strongly red (memes are an amplifier
            # — sector dump = trade dump regardless of SOL). Flat otherwise.
            # Analytics-only — NOT used to filter (yet).
            try:
                _sh1 = sol_features.get("sol_pc_h1")
                _sh4 = sol_features.get("sol_pc_h4")
                _bh1 = sol_features.get("btc_pc_h1")
                _msc = sol_features.get("meme_sector_pct_h24")
                if _sh1 is not None and _sh4 is not None:
                    if _msc is not None and _msc < -5.0:
                        sol_features["regime"] = "down"  # sector dump dominates
                    elif _sh1 > 0.3 and _sh4 > 0.5 and (_bh1 is None or _bh1 > -0.5) and (_msc is None or _msc > -2.0):
                        sol_features["regime"] = "up"
                    elif _sh1 < -0.5 or _sh4 < -1.5:
                        sol_features["regime"] = "down"
                    else:
                        sol_features["regime"] = "flat"
            except Exception:
                pass

            # ── Tier 2b: Jupiter quote asymmetry ──
            # Closest analog to "order book imbalance" on Solana AMMs.
            # Quote BUY (SOL→token at position size) and SELL (token→SOL at
            # equivalent token amount). Compare priceImpactPct. Higher sell
            # impact = sell-heavy pool. Two calls/signal, fail-open.
            jup_features: dict = {}
            try:
                import aiohttp as _aio
                _SOL_MINT = "So11111111111111111111111111111111111111112"
                _JUP_URL = "https://api.jup.ag/swap/v1/quote"
                sol_price_est = sol_5m[-1].close if sol_5m else 80.0
                buy_sol = self.position_usd / max(sol_price_est, 1.0)
                buy_lamports = max(int(buy_sol * 1e9), 1_000_000)
                params_buy = {
                    "inputMint": _SOL_MINT, "outputMint": token_address,
                    "amount": buy_lamports, "slippageBps": 300,
                }
                async with _aio.ClientSession() as _s:
                    async with _s.get(_JUP_URL, params=params_buy, timeout=_aio.ClientTimeout(total=8)) as _r:
                        buy_q = await _r.json() if _r.status == 200 else None
                if buy_q and buy_q.get("outAmount"):
                    buy_impact = float(buy_q.get("priceImpactPct") or 0) * 100
                    sell_amount = int(buy_q["outAmount"])
                    params_sell = {
                        "inputMint": token_address, "outputMint": _SOL_MINT,
                        "amount": sell_amount, "slippageBps": 300,
                    }
                    async with _aio.ClientSession() as _s:
                        async with _s.get(_JUP_URL, params=params_sell, timeout=_aio.ClientTimeout(total=8)) as _r:
                            sell_q = await _r.json() if _r.status == 200 else None
                    sell_impact = float(sell_q.get("priceImpactPct") or 0) * 100 if sell_q else 0.0
                    jup_features = {
                        "quote_buy_impact_pct": round(buy_impact, 4),
                        "quote_sell_impact_pct": round(sell_impact, 4),
                        "quote_asymmetry_pct": round(sell_impact - buy_impact, 4),
                    }
            except Exception as _e:
                logger.debug(f"[DipScanner] Jupiter asymmetry error: {_e}")

            # ── Tier 3: WS tick buffer (sub-minute resolution) ──
            # Reads from AxiomPriceFeed's per-token tick deque (price-only,
            # sub-second granularity). Candidates aren't pre-subscribed, so
            # buffer is usually empty for first signal but populates on
            # repeat encounters. Fail-open.
            tick_features: dict = {}
            if self.axiom_price_feed is not None:
                try:
                    ws30 = self.axiom_price_feed.get_tick_trend(token_address, 30)
                    ws60 = self.axiom_price_feed.get_tick_trend(token_address, 60)
                    ws120 = self.axiom_price_feed.get_tick_trend(token_address, 120)
                    ws_count_30 = self.axiom_price_feed.get_tick_count(token_address, 30)
                    ws_count_60 = self.axiom_price_feed.get_tick_count(token_address, 60)
                    tick_features = {
                        "ws_pc_30s":   round(ws30, 3) if ws30 is not None else None,
                        "ws_pc_60s":   round(ws60, 3) if ws60 is not None else None,
                        "ws_pc_120s":  round(ws120, 3) if ws120 is not None else None,
                        "ws_ticks_30s": ws_count_30,
                        "ws_ticks_60s": ws_count_60,
                    }
                    # Pre-subscribe so future encounters of this token have
                    # buffered ticks. No-op if already subscribed.
                    self.axiom_price_feed.subscribe_token(token_address)
                except Exception as _e:
                    logger.debug(f"[DipScanner] WS tick read error for {token_symbol}: {_e}")

            # ── Recent-trades capture ──
            # Fetch last ~30 trades to capture order-flow detail beyond the
            # aggregate bs_m5/bs_h1 ratios. Counts buys/sells, dollar volumes,
            # and the direction-string of the last 10 trades (e.g. "BBSBSSSS").
            # Single API call per signal-fire, cached 60s. Fail-open.
            recent_trades_features: dict = {}
            try:
                recent_trades = await self.gt_client.fetch_recent_trades(
                    pair_addr_for_1m, limit=30
                )
            except Exception as _e:
                logger.debug(f"[DipScanner] recent_trades error for {token_symbol}: {_e}")
                recent_trades = []
            if recent_trades:
                buys = [t for t in recent_trades if t.get("kind") == "buy"]
                sells = [t for t in recent_trades if t.get("kind") == "sell"]
                buys_usd = sum(t["volume_usd"] for t in buys)
                sells_usd = sum(t["volume_usd"] for t in sells)
                last10 = list(reversed(recent_trades[:10]))
                last10_dir = "".join("B" if t.get("kind") == "buy" else "S" for t in last10)
                # Tier 1 derivations on the same 30-trade fetch
                buys_size = [t["volume_usd"] for t in buys]
                sells_size = [t["volume_usd"] for t in sells]
                rt_max_buy = max(buys_size) if buys_size else 0.0
                rt_max_sell = max(sells_size) if sells_size else 0.0
                rt_avg_buy = (sum(buys_size) / len(buys_size)) if buys_size else 0.0
                rt_avg_sell = (sum(sells_size) / len(sells_size)) if sells_size else 0.0
                # Consecutive sells at the END of the stream (recent first)
                rt_consec_sells = 0
                for t in recent_trades:
                    if t.get("kind") == "sell":
                        rt_consec_sells += 1
                    else:
                        break
                # Consecutive buys at the END of the stream
                rt_consec_buys = 0
                for t in recent_trades:
                    if t.get("kind") == "buy":
                        rt_consec_buys += 1
                    else:
                        break
                # Recent-skew: last 10 vs trades 11-30. If the LAST 10 are
                # more sell-heavy than the older 20, distribution is
                # accelerating. Positive = buys accelerating, negative = sells.
                rt_recent_skew = 0.0
                if len(recent_trades) >= 20:
                    recent_b = sum(1 for t in recent_trades[:10] if t.get("kind") == "buy")
                    older_b = sum(1 for t in recent_trades[10:] if t.get("kind") == "buy")
                    older_n = len(recent_trades) - 10
                    rt_recent_skew = (recent_b/10) - (older_b/older_n if older_n else 0)
                # Sub-minute timing: parse timestamps to compute span and rate
                rt_time_span_secs = 0.0
                rt_trades_per_sec = 0.0
                rt_secs_since_last = 0.0
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    times = []
                    for t in recent_trades:
                        ts = t.get("ts")
                        if ts:
                            try:
                                times.append(_dt.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
                            except Exception:
                                pass
                    if len(times) >= 2:
                        rt_time_span_secs = max(times) - min(times)
                        rt_trades_per_sec = len(times) / rt_time_span_secs if rt_time_span_secs > 0 else 0.0
                        rt_secs_since_last = max(0.0, _dt.now(_tz.utc).timestamp() - max(times))
                except Exception:
                    pass
                recent_trades_features = {
                    "rt_n": len(recent_trades),
                    "rt_buys_n": len(buys),
                    "rt_sells_n": len(sells),
                    "rt_buys_usd": round(buys_usd, 2),
                    "rt_sells_usd": round(sells_usd, 2),
                    "rt_dollar_imbalance": (
                        round((buys_usd - sells_usd) / (buys_usd + sells_usd), 3)
                        if (buys_usd + sells_usd) > 0 else 0.0
                    ),
                    "last10_dir": last10_dir,
                    "rt_max_buy_usd": round(rt_max_buy, 2),
                    "rt_max_sell_usd": round(rt_max_sell, 2),
                    "rt_avg_buy_usd": round(rt_avg_buy, 2),
                    "rt_avg_sell_usd": round(rt_avg_sell, 2),
                    "rt_consec_sells": rt_consec_sells,
                    "rt_consec_buys": rt_consec_buys,
                    "rt_recent_skew": round(rt_recent_skew, 3),
                    "rt_time_span_secs": round(rt_time_span_secs, 1),
                    "rt_trades_per_sec": round(rt_trades_per_sec, 3),
                    "rt_secs_since_last": round(rt_secs_since_last, 1),
                }

            # Batch 1 entry-meta — anything dip_scanner has at this moment that's
            # nice-to-have for analysis but doesn't merit its own Position field.
            pair_age_hours = (now_ms - created_ms) / 3_600_000 if created_ms > 0 else 0.0
            peak_h24_6h = max((entry[1] for entry in hist), default=pc_h24)
            cycles_seen = len(hist)

            # Pre-entry momentum trajectory (Gap 3, 2026-05-02). Captures the
            # SHAPE of how the token arrived at its current state, not just
            # the snapshot. Two tokens with identical pc_h1=-3% can have
            # opposite outcomes depending on whether h1 was +30% 30 min ago
            # (fresh pullback in active pump) or -2% (extended distribution
            # already underway). cycles_seen + peak_h24_6h capture magnitude
            # but not derivative.
            #
            # Features below are best-effort: legacy history entries (loaded
            # from old-format /data/h24_history.json) have None for h1/h6
            # and are skipped when computing those derivatives. ts is always
            # present, so pc_h24 trajectory is always derivable.
            #
            # Logged-only — not used as a filter. Forward analysis bucket-by-
            # trajectory will inform whether/how to gate on it.
            trajectory_features: dict = {}
            if len(hist) >= 2:
                # 30 min lookback: most recent entry that's at least 5 min old
                # (avoids same-cycle noise) and at most 45 min old (loose cap
                # so we still get a reading on tokens we just started watching).
                _t30_min = wall_now - 1800
                _t30_max = wall_now - 300
                _candidates_h1 = [e for e in hist if _t30_min - 900 <= e[0] <= _t30_max and e[2] is not None]
                _candidates_h6 = [e for e in hist if _t30_min - 900 <= e[0] <= _t30_max and e[3] is not None]
                _candidates_h24 = [e for e in hist if _t30_min - 900 <= e[0] <= _t30_max]
                # Pick the entry closest to 30 min ago in each pool.
                def _closest_to_target(items, target_ts):
                    return min(items, key=lambda e: abs(e[0] - target_ts)) if items else None
                _e_h24 = _closest_to_target(_candidates_h24, _t30_min)
                _e_h1 = _closest_to_target(_candidates_h1, _t30_min)
                _e_h6 = _closest_to_target(_candidates_h6, _t30_min)
                if _e_h24:
                    trajectory_features["pc_h24_lookback"] = round(_e_h24[1], 2)
                    trajectory_features["pc_h24_change_since_lookback"] = round(pc_h24 - _e_h24[1], 2)
                    trajectory_features["lookback_secs"] = round(wall_now - _e_h24[0], 0)
                if _e_h1 and _e_h1[2] is not None:
                    trajectory_features["pc_h1_lookback"] = round(_e_h1[2], 2)
                    trajectory_features["pc_h1_change_since_lookback"] = round(pc_h1 - _e_h1[2], 2)
                if _e_h6 and _e_h6[3] is not None:
                    trajectory_features["pc_h6_lookback"] = round(_e_h6[3], 2)
                    trajectory_features["pc_h6_change_since_lookback"] = round(pc_h6 - _e_h6[3], 2)
                # Time since each peak in the 6h window — distinguishes
                # "just rolled over" from "been deteriorating for hours".
                _h1_entries = [e for e in hist if e[2] is not None]
                _h6_entries = [e for e in hist if e[3] is not None]
                if _h1_entries:
                    _peak_h1_e = max(_h1_entries, key=lambda e: e[2])
                    trajectory_features["h1_peak_in_window"] = round(_peak_h1_e[2], 2)
                    trajectory_features["time_since_h1_peak_secs"] = round(wall_now - _peak_h1_e[0], 0)
                if _h6_entries:
                    _peak_h6_e = max(_h6_entries, key=lambda e: e[3])
                    trajectory_features["h6_peak_in_window"] = round(_peak_h6_e[3], 2)
                    trajectory_features["time_since_h6_peak_secs"] = round(wall_now - _peak_h6_e[0], 0)
                _peak_h24_e = max(hist, key=lambda e: e[1])
                trajectory_features["time_since_h24_peak_secs"] = round(wall_now - _peak_h24_e[0], 0)

            # Observational: high cycles_seen correlates with fast stops (50-100
            # bucket: 50-62% WR, ~-$9/trade in our 127-trade dataset). Logged but
            # not enforced — gathering live evidence before adding a hard filter.
            if cycles_seen >= 60:
                c["obs_high_cycles"] += 1
                logger.warning(
                    f"[DipScanner] OBSERVATIONAL: {token_symbol} cycles_seen={cycles_seen} "
                    f"(>=60 historically -EV; not blocking)"
                )

            # Filter A — DEPRECATED, no longer enforced. Validation on the full
            # 540-pair lifetime dataset (vs the original 32-trade post-rewrite
            # sample) showed Filter A's PASS bucket has a *lower* WR than its
            # BLOCK bucket (40% vs 49%) and a -$134 PASS total. The original
            # bounds were curve-fit to spare a single winning token (MUSHU
            # $168k liq) — that's overfitting on n=32 with 3 tunable
            # parameters. Liquidity is a mediating variable, not the causal
            # signal; entry timing relative to the pump cycle is. Filter
            # retained as a shadow field for forward correlation against
            # filter_real_dip outcomes — kept until ~30 post-deploy trades
            # confirm the swap is net-positive, then removed entirely.
            _liq_for_filter = float(liq_usd or 0)
            _peak_for_filter = float(peak_h24_6h)
            _filter_a_block_reasons = []
            if _liq_for_filter < 167_000:
                _filter_a_block_reasons.append(f"liq=${_liq_for_filter/1000:.0f}k<167k")
            elif _liq_for_filter > 967_000:
                _filter_a_block_reasons.append(f"liq=${_liq_for_filter/1000:.0f}k>967k")
            if _peak_for_filter > 200:
                _filter_a_block_reasons.append(f"peak={_peak_for_filter:.0f}%>200%")
            _filter_a_verdict = "BLOCK" if _filter_a_block_reasons else "PASS"
            c[f"filter_a_{_filter_a_verdict.lower()}"] = c.get(f"filter_a_{_filter_a_verdict.lower()}", 0) + 1
            # NO `continue` — Filter A is shadow-only.

            # Filter peak-floor — ENFORCED 2026-05-02, threshold relaxed
            # 2026-05-02 evening from 20% to 5% after winner-verification
            # showed the 20% floor was blocking 7+ April 28 winners
            # (BULL peak=7%, EITHER peak=19%, BURNIE peak=19%, MAGA peak=11%,
            # ASTEROID peak=8%, BULL peak=17%, LOL peak=9%). Original 20%
            # threshold was tuned against the May 2 morning winner cohort
            # (peaks 56%-1324%) — over-fit to that regime.
            #
            # The 5% threshold still encodes the structural requirement
            # that dip_buy needs SOME recent move to dip from. 5% isolates
            # the truly-flat-range entries (Wish at +0.75%) without
            # blocking small-pump winners. filter_two_pattern's
            # h24_ratio_to_peak >= 0.60 (Pattern A) requirement does
            # most of the structural work now.
            if float(peak_h24_6h) < 5.0:
                c["filter_peak_floor_block"] = c.get("filter_peak_floor_block", 0) + 1
                logger.info(
                    f"[DipScanner] BLOCKED by filter_peak_floor: {token_symbol} "
                    f"peak_h24_6h={float(peak_h24_6h):+.1f}% < +5% (no recent move)"
                )
                if not self.baseline_mode:
                    continue

            # Filter post-pump-corpse — ENFORCED 2026-05-02.
            # Catches "post-pump corpse" entries: token sitting above middle
            # of its 1h range AND already dumped >70% from its 24h peak. Not
            # a dip in an active uptrend — a corpse with a small bounce.
            #
            # Trigger case: Goblin 04:27 UTC 2026-05-02 — peak_h24_6h=254%
            # but h24_ratio_to_peak=0.24 (down 76% from peak), 1h_rng=0.63.
            # Stopped out 3.5 min later at -10%.
            #
            # Methodology: systematic Cohen's d scan across all numeric
            # entry_meta fields. pct_in_1h_range had |d|=1.01 (largest) —
            # winners enter near 35% of 1h range, losers near 58-71%.
            # Compound with h24_ratio_to_peak<0.30 isolates the corpse shape
            # from "active runner with mid-range entry" (which produced our
            # biggest wins: ORCA, ooo, EITHER, BULL — all ratio>0.40).
            #
            # Lifetime validation: BLOCK n=4-5, 0% WR, total -$55 (we save
            # $55 by enforcing). Winner Regression Set: zero winners blocked.
            # Also catches the historic SCAM 04-28 catastrophe (-$52.70).
            #
            # Fail-open if pct_in_1h_range missing (5m fetch failed) — the
            # feature has ~50% coverage. Don't penalize on missing data.
            _pct_in_1h_range = range_features.get("pct_in_1h_range")
            _h24_ratio = (pc_h24 / float(peak_h24_6h)) if float(peak_h24_6h) > 0 else 1.0
            _filter_corpse_block_reasons: list = []
            if (
                _pct_in_1h_range is not None
                and _pct_in_1h_range > 0.55
                and _h24_ratio < 0.30
            ):
                _filter_corpse_block_reasons.append(
                    f"pct_in_1h_range={_pct_in_1h_range:.2f}>0.55 AND "
                    f"h24_ratio_to_peak={_h24_ratio:.2f}<0.30 (post-pump corpse)"
                )
            _filter_corpse_verdict = "BLOCK" if _filter_corpse_block_reasons else "PASS"
            c[f"filter_corpse_{_filter_corpse_verdict.lower()}"] = c.get(
                f"filter_corpse_{_filter_corpse_verdict.lower()}", 0
            ) + 1
            if _filter_corpse_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_corpse: {token_symbol} "
                    f"reasons={','.join(_filter_corpse_block_reasons)}"
                )
                if not self.baseline_mode:
                    continue

            # Filter fake-bounce — ENFORCED 2026-05-02.
            # Catches "1m green pulse on dead volume" pattern: last 1m
            # candle closed >1.75% green BUT volume_spike < 0.30 (volume
            # <30% of trailing average). The 1m green is air, not real
            # buying — a fake reversal inside a sustained down-move that
            # doesn't hold.
            #
            # Trigger case: NKT 02:04 UTC 2026-05-02 — 1m_close=+1.81%,
            # vol=0.17. Stopped out 2 min later at -10%. ASTEROID 04-27
            # (twice) was the historic disaster: +4.47% / 0.04 vol → -$91,
            # +5.98% / 0.10 vol → -$54.
            #
            # Methodology: Cohen's d scan comparing never-green losers
            # (n=105) vs big winners (n=93). 1m_last_close_pct flipped:
            # winners' last 1m close median -0.46% (still red), losers'
            # -0.07% (basically flat). Compound with vol_spike < 0.30
            # isolates the "fake bounce" pattern from normal mid-dip
            # green pulses (which winners often show).
            #
            # Lifetime validation: BLOCK n=3, 0% WR, total -$148. Winner
            # Regression Set: zero blocked. SCRIBBELON 01:09 (+$3.62,
            # 1m_close=+1.47) clears threshold by 0.28; BOAR 19:24
            # (+$67.60, vol_spike=0.66) clears by both vectors. Filter
            # is narrow — precision-focused, not recall.
            #
            # Fail-open if 1m features missing — m1_features can be
            # empty when the 1m fetch fails.
            _m1_lcp = m1_features.get("1m_last_close_pct")
            _m1_vs = m1_features.get("1m_volume_spike")
            _filter_fake_bounce_block_reasons: list = []
            if (
                _m1_lcp is not None
                and _m1_vs is not None
                and _m1_lcp > 1.75
                and _m1_vs < 0.30
            ):
                _filter_fake_bounce_block_reasons.append(
                    f"1m_close={_m1_lcp:+.2f}%>1.75 AND "
                    f"1m_vol_spike={_m1_vs:.2f}<0.30 (fake bounce on dead volume)"
                )
            _filter_fake_bounce_verdict = "BLOCK" if _filter_fake_bounce_block_reasons else "PASS"
            c[f"filter_fake_bounce_{_filter_fake_bounce_verdict.lower()}"] = c.get(
                f"filter_fake_bounce_{_filter_fake_bounce_verdict.lower()}", 0
            ) + 1
            if _filter_fake_bounce_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_fake_bounce: {token_symbol} "
                    f"reasons={','.join(_filter_fake_bounce_block_reasons)}"
                )
                if not self.baseline_mode:
                    continue

            # Filter real-dip-3 — ENFORCED. Validated on the full 540-pair
            # lifetime dataset (held-out test, not the same data the filter
            # was tuned on). Hypothesis: dip-buy works only when there is an
            # actual pullback to buy; entries with flat/mild momentum in
            # both windows are buying distribution, not dips.
            #
            # Rule: BLOCK if pc5m > -3% AND pc1h > -3% (no real pullback in
            # either window). PASS if at least one window shows ≤-3%.
            #
            # Lifetime stats — PASS: n=271, 53% WR, median +3.38%, total
            # -$295 (vs baseline -$1252; 76% loss reduction). BLOCK: n=269,
            # 41% WR, total -$957. Robustness: serial loss-after-loss 17.4%
            # (vs 50% baseline; no clustering). Top-3-token removal: PASS
            # -$295 → +$179 total, BLOCK -$957 → -$421 — discrimination
            # amplifies under token-removal stress, doesn't disappear.
            #
            # Exemptions (both added 2026-05-02 evening):
            #   1. mcap > $2M — big-caps don't dip 3-5% intraday like
            #      memes; "no real pullback" misclassifies their normal
            #      tape as bad entries.
            #   2. pc_h1 > +5% — strongly-uptrending tokens with shallow
            #      m5 dips are the "active uptrend continuation" pattern,
            #      not a corpse. Winner-verification surfaced this shape
            #      across small-cap winners: BOAR pc_m5=-2.0/h1=+20.4
            #      ($+67), BELKA pc_m5=-1.1/h1=+6.9 ($+53),
            #      LOL pc_m5=-0.5/h1=+7.2 ($+22), Lobstar h1=+4.2,
            #      BULL h1=+4.5 — all blocked under mcap-only exemption.
            #      pc_h1 > +5% means the 1h timeframe is in clear uptrend;
            #      m5 negative is just intraday noise.
            #
            # filter_two_pattern still gates the actual entry quality on
            # exempted tokens via Pattern A or B match.
            _entry_mcap = float(mcap or 0)
            _real_dip_3_exempt = (_entry_mcap > 2_000_000) or (pc_h1 > 5.0)
            _filter_real_dip_3_block_reasons = []
            if pc_m5 > -3 and pc_h1 > -3:
                _filter_real_dip_3_block_reasons.append(
                    f"5m={pc_m5:+.2f}%>-3 AND 1h={pc_h1:+.2f}%>-3 (no real pullback)"
                )
            _filter_real_dip_3_verdict = "BLOCK" if _filter_real_dip_3_block_reasons else "PASS"
            if _real_dip_3_exempt and _filter_real_dip_3_verdict == "BLOCK":
                _filter_real_dip_3_verdict = "EXEMPT"  # big-cap exemption
            c[f"filter_real_dip_3_{_filter_real_dip_3_verdict.lower()}"] = c.get(
                f"filter_real_dip_3_{_filter_real_dip_3_verdict.lower()}", 0
            ) + 1
            if _filter_real_dip_3_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_real_dip_3: {token_symbol} "
                    f"5m={pc_m5:+.2f}% 1h={pc_h1:+.2f}% "
                    f"reasons={','.join(_filter_real_dip_3_block_reasons)}"
                )
                if not self.baseline_mode:
                    continue
            if _filter_real_dip_3_verdict == "EXEMPT":
                logger.info(
                    f"[DipScanner] real-dip-3 EXEMPT (big-cap): {token_symbol} "
                    f"mcap=${_entry_mcap/1e6:.1f}M>$10M "
                    f"5m={pc_m5:+.2f}% 1h={pc_h1:+.2f}%"
                )

            # Filter real-dip-5 — SHADOW. Tighter version of real-dip-3
            # requiring a sharper pullback in at least one window. Lifetime
            # stats — PASS: n=186, 56% WR, median +6.07%, **total +$107**
            # (only filter that flips PASS to positive on full dataset).
            # Top-3-token removal lifts PASS to +$574 / 59% WR. Volume
            # tradeoff: roughly 1-in-3 signals pass vs 1-in-2 for real-dip-3.
            # Logged-only for now; promotion candidate after ~30 post-deploy
            # trades validate the differential holds forward.
            _filter_real_dip_5_block_reasons = []
            if pc_m5 > -5 and pc_h1 > -5:
                _filter_real_dip_5_block_reasons.append(
                    f"5m={pc_m5:+.2f}%>-5 AND 1h={pc_h1:+.2f}%>-5"
                )
            _filter_real_dip_5_verdict = "BLOCK" if _filter_real_dip_5_block_reasons else "PASS"
            c[f"filter_real_dip_5_{_filter_real_dip_5_verdict.lower()}"] = c.get(
                f"filter_real_dip_5_{_filter_real_dip_5_verdict.lower()}", 0
            ) + 1

            # Filter 1M — SHADOW MODE: tests whether 1-minute momentum signals
            # explain the time-of-day P&L pattern. Hour-of-day analysis showed
            # 8am+12pm CT lose money; comparing those entries to 9-11am+1pm CT
            # entries showed bad-hour entries had:
            #   - 1m_cum_3min_pct median -1.10% vs good -0.48% (steeper decline)
            #   - 1m_volume_spike median 0.38 vs good 0.63 (volume dying)
            # Hypothesis: bad hours are bad because of low-liquidity noise
            # producing fake dips, not the clock itself. If we filter on the
            # 1m signals directly, time-of-day stops mattering. Sample is small
            # (6 vs 17 with meta) — shadow first, validate forward.
            _m1_cum = m1_features.get("1m_cum_3min_pct")
            _m1_vol_spike = m1_features.get("1m_volume_spike")
            _filter_1m_block_reasons = []
            if _m1_cum is not None and _m1_cum < -1.0:
                _filter_1m_block_reasons.append(f"1m_cum3={_m1_cum:.2f}%<-1.0%")
            if _m1_vol_spike is not None and _m1_vol_spike < 0.40:
                _filter_1m_block_reasons.append(f"1m_vol_spike={_m1_vol_spike:.2f}<0.40")
            _filter_1m_verdict = "BLOCK" if _filter_1m_block_reasons else "PASS"
            c[f"filter_1m_{_filter_1m_verdict.lower()}"] = c.get(f"filter_1m_{_filter_1m_verdict.lower()}", 0) + 1
            logger.info(
                f"[DipScanner] FILTER_1M_SHADOW: {token_symbol} "
                f"1m_cum3={_m1_cum if _m1_cum is not None else 'n/a'} "
                f"1m_vol_spike={_m1_vol_spike if _m1_vol_spike is not None else 'n/a'} "
                f"verdict={_filter_1m_verdict}"
                + (f" reasons={','.join(_filter_1m_block_reasons)}" if _filter_1m_block_reasons else "")
            )

            # Filter FOFAR-confluence — ENFORCED 2026-05-02.
            # Catches the "rolling-over topping pattern" where multiple
            # weak-trend signals stack on the same entry. None of the
            # individual shadow filters caught FOFAR cleanly, but 5/5
            # fired together — the confluence IS the signal.
            #
            # Score: +1 each for
            #   bs_m5 < 0.7              (sellers winning current 5m)
            #   filter_1m_verdict=BLOCK  (1m cum dump or dead vol)
            #   pct_in_1h_range > 0.55   (above 1h midline = no real dip)
            #   5m_lower_highs >= 7      (sustained downtrend structure)
            #   token_ema_verdict=BLOCK  (1H EMA slope < -1.5%)
            #
            # Trigger case: FOFAR 15:45 UTC 2026-05-02 — bs_m5=0.54,
            # 1m=BLOCK, 1h_rng=0.573, 5m_lh=8, ema=BLOCK → 5/5. Stopped
            # out 18 min later at -8%.
            #
            # Lifetime validation: score>=4 → n=1 BLOCK (a +$2.19 winner
            # ZEREBRO 12:48), 0 Winner Regression Set blocks. Tighter
            # threshold than score>=3 (which would block 2 small-$
            # regression winners ~$5 total). Trade-off accepted: filter
            # is precision-tuned to catch FOFAR-class confluence; small
            # historical winner cost is OK because the pattern is the
            # one we're trying to catch going forward.
            #
            # Fail-open on missing features — score is computed only on
            # the features that ARE present, threshold still 4.
            _fofar_score = 0
            _fofar_components: list = []
            _fofar_bs_m5 = float(ratio_m5) if ratio_m5 != float("inf") else None
            if _fofar_bs_m5 is not None and _fofar_bs_m5 < 0.7:
                _fofar_score += 1
                _fofar_components.append(f"bs_m5={_fofar_bs_m5:.2f}<0.7")
            if _filter_1m_verdict == "BLOCK":
                _fofar_score += 1
                _fofar_components.append("filter_1m=BLOCK")
            _fofar_1h_rng = range_features.get("pct_in_1h_range")
            if _fofar_1h_rng is not None and _fofar_1h_rng > 0.55:
                _fofar_score += 1
                _fofar_components.append(f"1h_rng={_fofar_1h_rng:.2f}>0.55")
            _fofar_5m_lh = range_features.get("5m_lower_highs")
            if _fofar_5m_lh is not None and _fofar_5m_lh >= 7:
                _fofar_score += 1
                _fofar_components.append(f"5m_lh={_fofar_5m_lh}>=7")
            _fofar_ema = range_features.get("token_ema_verdict")
            if _fofar_ema == "BLOCK":
                _fofar_score += 1
                _fofar_components.append("token_ema=BLOCK")
            _filter_fofar_block_reasons: list = []
            if _fofar_score >= 4:
                _filter_fofar_block_reasons.append(
                    f"confluence_score={_fofar_score}/5 [{','.join(_fofar_components)}]"
                )
            _filter_fofar_verdict = "BLOCK" if _filter_fofar_block_reasons else "PASS"
            c[f"filter_fofar_{_filter_fofar_verdict.lower()}"] = c.get(
                f"filter_fofar_{_filter_fofar_verdict.lower()}", 0
            ) + 1
            if _filter_fofar_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_fofar: {token_symbol} "
                    f"reasons={','.join(_filter_fofar_block_reasons)}"
                )
                if not self.baseline_mode:
                    continue

            # Filter two-pattern positive criterion — ENFORCED 2026-05-02.
            #
            # The bot's filter stack until now was a defensive negative
            # gate ("block IF X"); winning entries were anything that
            # didn't trip a filter. Systematic Cohen's-d analysis on 64
            # post-2026-04-29T16:00 paired trades revealed the dataset
            # has TWO winning archetypes, and a positive joint-criterion
            # cleanly separates them from losers.
            #
            # Pattern A — Real Dip Recovery
            #   pct_in_1h_range  < 0.40  (near the 1h low)
            #   h24_ratio_to_peak >= 0.60 (still close to recent peak)
            #   1m_consec_red    <= 1    (not in 1m freefall)
            #
            # Pattern B — Strength Continuation
            #   h24_ratio_to_peak >= 0.85 (right under recent peak)
            #   1m_consec_red    <= 1
            #
            # Buy IF (all of A) OR (all of B). Block otherwise.
            #
            # Performance vs current bot on the 64-trade window:
            #   Current: 64 trades, 31% WR, -$18.02 net
            #   Filter:  30 trades, 57% WR, +$18.69 net (counterfactual)
            #   Win recall 85% (17/20), Loss rejection 72% (18/25)
            #
            # Winner Regression Set: ALL 5 in-dataset entries pass
            # (EITHER 00:25 → A, Goblin 00:58 → A, SCRIBBELON 01:09 → B,
            # SCRIBBELON 02:09 → A, GRUMP 05:43 → B). Temporal split
            # holds: TRAIN (44 trades) 57% WR, TEST (20 trades) 56% WR
            # with 100% win recall.
            #
            # Volume cost: ~50% reduction. Accepted — net swing +$36 on
            # the window vs current bot. Discipline > volume per
            # 2026-05-02 user direction post-SCAM rebuy.
            #
            # Fail-CLOSED if features missing — the dominant
            # discriminator pct_in_1h_range can be absent when the 5m
            # fetch fails (~14% of trades historically); 1m_consec_red
            # can be absent when 1m fetch fails. Earlier fail-open
            # design defeated the filter on those trades (FOFAR 18:05
            # 2026-05-02 slipped through with 1h_rng=None and
            # h24_r=0.46 — neither pattern would have matched). Better
            # to skip than to enter blind. The 5m fetch succeeds often
            # enough that fail-closed still leaves meaningful frequency.
            _tp_1h_rng = range_features.get("pct_in_1h_range")
            _tp_h24_ratio = (pc_h24 / float(peak_h24_6h)) if float(peak_h24_6h) > 0 else 1.0
            _tp_consec_red = m1_features.get("1m_consec_red", 0)
            _tp_pattern_a = False
            _tp_pattern_b = False
            _tp_can_evaluate = (_tp_1h_rng is not None)
            if _tp_can_evaluate:
                _tp_pattern_a = (
                    _tp_1h_rng < 0.40
                    and _tp_h24_ratio >= 0.60
                    and _tp_consec_red <= 1
                )
                # Pattern B tightened 2026-05-02 from consec_red<=1 to ==0
                # after EITHER 19:42 stopped (-$1.79) — same shape as
                # EITHER 01:18 yesterday (-$2.10), both with red=1. Loser
                # cohort review: 5/6 Pattern B losses have red=1 and
                # peak_pnl=0 (never green). Winners SCRIBBELON 01:09 and
                # GRUMP 05:43 both have red=0. Trade-off: gives up FOFAR
                # 11:51 (+$4 winner with red=1) to block 5 known losers
                # (~$13 saved). Pattern A unchanged — its red<=1 threshold
                # is correct because real-dip recoveries can show one red
                # candle mid-bounce (e.g. EITHER 00:25, SCRIBBELON 02:09).
                _tp_pattern_b = (
                    _tp_h24_ratio >= 0.85
                    and _tp_consec_red == 0
                )
            if not _tp_can_evaluate:
                _filter_two_pattern_verdict = "BLOCK"
                _filter_two_pattern_reason = "fail-closed (1h_rng missing)"
            elif not (_tp_pattern_a or _tp_pattern_b):
                _filter_two_pattern_verdict = "BLOCK"
                _filter_two_pattern_reason = (
                    f"neither A nor B "
                    f"[1h_rng={_tp_1h_rng:.2f} h24_r={_tp_h24_ratio:.2f} red={_tp_consec_red}]"
                )
            else:
                _filter_two_pattern_verdict = "PASS"
                _filter_two_pattern_reason = (
                    "A" if _tp_pattern_a and not _tp_pattern_b
                    else "B" if _tp_pattern_b and not _tp_pattern_a
                    else "AB"
                )
            c[f"filter_two_pattern_{_filter_two_pattern_verdict.lower()}"] = c.get(
                f"filter_two_pattern_{_filter_two_pattern_verdict.lower()}", 0
            ) + 1
            if _filter_two_pattern_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_two_pattern: {token_symbol} "
                    f"reason={_filter_two_pattern_reason}"
                )
                if not self.baseline_mode:
                    continue

            txns_h1_total = b_h1 + s_h1
            avg_trade_size_h1 = (vol_h1 / txns_h1_total) if txns_h1_total > 0 else 0.0

            # Bot-state context at signal time. Captures the conditions the
            # bot itself was in when this entry fired, so forward analysis
            # can correlate trade outcomes with concurrency, pacing, and
            # daily-PnL state. Test 4 (concurrent positions) showed slot 3
            # was the worst — needs this field on every record. Test 5
            # (reentry-graveyard) showed 30-60min reentry-after-loss was
            # -EV — needs time_since_last_close_on_token to validate forward.
            try:
                _now_mono = time.monotonic()
                _now_wall = time.time()
                _concur_dip = sum(
                    1 for _p in self.open_positions_ref.values()
                    if (getattr(_p, "strategy", "") or "") == "dip_buy"
                )
                _bot_state = {"concurrent_positions_at_entry": _concur_dip}
                if self._last_buy_time > 0:
                    _bot_state["time_since_last_buy_secs"] = round(_now_mono - self._last_buy_time, 1)
                # Per-token "last close" approximation: dip_loss_cooldown
                # entries record (ts, secs) at the moment of close. Pruned
                # to the active window — so non-None means token closed
                # within ~6h (stop/vol-death) or ~30min (other closes).
                # Sufficient for the reentry-graveyard analysis.
                try:
                    _cooldown = self.trader._dip_loss_cooldown.get(token_address.lower())
                    if isinstance(_cooldown, (list, tuple)) and len(_cooldown) == 2:
                        _bot_state["time_since_last_close_on_token_secs"] = round(_now_wall - float(_cooldown[0]), 1)
                except Exception:
                    pass
                # Prior-buy count for this token (lifetime, not session).
                try:
                    _bot_state["prior_buys_for_token"] = int(
                        self.trader.reentry.buy_counts.get(token_address.lower(), 0) or 0
                    )
                except Exception:
                    pass
                # RiskManager state — daily_pnl + trades_today
                try:
                    _rm = self.trader.risk_manager
                    _bot_state["daily_pnl_at_entry"] = round(float(getattr(_rm, "daily_pnl", 0.0) or 0.0), 2)
                    _bot_state["trades_today_at_entry"] = int(getattr(_rm, "trades_today", 0) or 0)
                    _bot_state["available_capital_at_entry"] = round(float(getattr(_rm, "available_capital", 0.0) or 0.0), 2)
                except Exception:
                    pass
            except Exception as _e:
                logger.debug(f"[DipScanner] bot-state capture error: {_e}")
                _bot_state = {}

            # Chart-reading shadow features (Phases 0-6, shipped 2026-05-02
            # evening). One async call per signal that runs candle pattern
            # detection, multi-timeframe trend alignment, support/resistance
            # analysis, volume-at-price profile, and chart pattern recognition.
            # Internally re-uses the GT 60s cache, so duplicate fetches with
            # the existing 1m/5m/15m calls above are free.
            #
            # SHADOW MODE — populated into entry_meta but NOT used as a gate.
            # ~50-100 forward trades needed to validate composite_score and
            # individual phase signals against outcomes before any chart
            # feature graduates to enforced filter status.
            _chart_ctx_dict: dict = {}
            try:
                from feeds.chart_reader import read_chart
                # Reuse the candles already fetched above — zero extra GT calls.
                _chart_ctx = await read_chart(self.gt_client, pair_addr_for_1m, chart_data=_chart_data)
                _chart_ctx_dict = {
                    "chart_score": _chart_ctx.composite_score,
                    "chart_verdict": _chart_ctx.composite_verdict,
                    "chart_reasons": _chart_ctx.composite_reasons,
                    "chart_full_coverage": _chart_ctx.has_full_coverage,
                    "chart_candle_confluence": _chart_ctx.candle_confluence,
                    "chart_candle_5m_pattern": _chart_ctx.candle_5m.get("latest_pattern"),
                    "chart_candle_15m_pattern": _chart_ctx.candle_15m.get("latest_pattern"),
                    "chart_mtf_alignment": _chart_ctx.mtf.get("alignment"),
                    "chart_mtf_score": _chart_ctx.mtf.get("score"),
                    "chart_mtf_verdicts": _chart_ctx.mtf.get("verdicts"),
                    "chart_sr_5m_at_support": _chart_ctx.sr_5m.get("at_support"),
                    "chart_sr_5m_at_resistance": _chart_ctx.sr_5m.get("at_resistance"),
                    "chart_sr_5m_below_broken": _chart_ctx.sr_5m.get("below_broken_support"),
                    "chart_sr_5m_support_strength": _chart_ctx.sr_5m.get("support_strength"),
                    "chart_sr_5m_support_pct_below": _chart_ctx.sr_5m.get("nearest_support_pct_below"),
                    "chart_sr_15m_at_support": _chart_ctx.sr_15m.get("at_support"),
                    "chart_sr_15m_at_resistance": _chart_ctx.sr_15m.get("at_resistance"),
                    "chart_sr_15m_support_strength": _chart_ctx.sr_15m.get("support_strength"),
                    "chart_vp_above_poc": _chart_ctx.vp_5m.get("current_above_poc"),
                    "chart_vp_at_hvn": _chart_ctx.vp_5m.get("at_hvn"),
                    "chart_vp_in_lvn": _chart_ctx.vp_5m.get("in_lvn"),
                    "chart_vp_poc_distance_pct": _chart_ctx.vp_5m.get("poc_distance_pct"),
                    "chart_pattern_5m": _chart_ctx.pattern_5m.get("pattern"),
                    "chart_pattern_5m_conf": _chart_ctx.pattern_5m.get("confidence"),
                    "chart_pattern_5m_dir": _chart_ctx.pattern_5m.get("direction"),
                    "chart_pattern_15m": _chart_ctx.pattern_15m.get("pattern"),
                    "chart_pattern_15m_conf": _chart_ctx.pattern_15m.get("confidence"),
                    "chart_pattern_15m_dir": _chart_ctx.pattern_15m.get("direction"),
                    # Phase 7 — trendlines & channels per timeframe
                    "chart_trendline_5m_verdict": _chart_ctx.trendlines_5m.get("trendline_verdict"),
                    "chart_trendline_5m_breakout_up": _chart_ctx.trendlines_5m.get("breakout_above_resistance"),
                    "chart_trendline_5m_breakdown": _chart_ctx.trendlines_5m.get("breakout_below_support"),
                    "chart_trendline_5m_in_channel": _chart_ctx.trendlines_5m.get("in_channel"),
                    "chart_trendline_5m_channel_pos": _chart_ctx.trendlines_5m.get("channel_position_pct"),
                    "chart_trendline_5m_channel_slope": _chart_ctx.trendlines_5m.get("channel_slope_type"),
                    "chart_trendline_5m_pct_to_resistance": _chart_ctx.trendlines_5m.get("pct_to_resistance"),
                    "chart_trendline_5m_pct_to_support": _chart_ctx.trendlines_5m.get("pct_to_support"),
                    "chart_trendline_15m_verdict": _chart_ctx.trendlines_15m.get("trendline_verdict"),
                    "chart_trendline_15m_breakout_up": _chart_ctx.trendlines_15m.get("breakout_above_resistance"),
                    "chart_trendline_15m_breakdown": _chart_ctx.trendlines_15m.get("breakout_below_support"),
                    "chart_trendline_15m_in_channel": _chart_ctx.trendlines_15m.get("in_channel"),
                    "chart_trendline_15m_channel_pos": _chart_ctx.trendlines_15m.get("channel_position_pct"),
                    "chart_trendline_15m_channel_slope": _chart_ctx.trendlines_15m.get("channel_slope_type"),
                    "chart_trendline_1h_verdict": _chart_ctx.trendlines_1h.get("trendline_verdict"),
                    "chart_trendline_1h_breakout_up": _chart_ctx.trendlines_1h.get("breakout_above_resistance"),
                    "chart_trendline_1h_breakdown": _chart_ctx.trendlines_1h.get("breakout_below_support"),
                    "chart_trendline_1h_in_channel": _chart_ctx.trendlines_1h.get("in_channel"),
                    "chart_trendline_1h_channel_pos": _chart_ctx.trendlines_1h.get("channel_position_pct"),
                    # Phase 8 — market structure (BOS/CHoCH) per timeframe
                    "chart_structure_5m_verdict": _chart_ctx.structure_5m.get("structure_verdict"),
                    "chart_structure_5m_state": _chart_ctx.structure_5m.get("current_structure"),
                    "chart_structure_5m_recent_bos_dir": (_chart_ctx.structure_5m.get("recent_bos") or {}).get("direction"),
                    "chart_structure_5m_recent_choch_dir": (_chart_ctx.structure_5m.get("recent_choch") or {}).get("direction"),
                    "chart_structure_5m_swing_count": _chart_ctx.structure_5m.get("swing_count"),
                    "chart_structure_15m_verdict": _chart_ctx.structure_15m.get("structure_verdict"),
                    "chart_structure_15m_state": _chart_ctx.structure_15m.get("current_structure"),
                    "chart_structure_15m_recent_bos_dir": (_chart_ctx.structure_15m.get("recent_bos") or {}).get("direction"),
                    "chart_structure_15m_recent_choch_dir": (_chart_ctx.structure_15m.get("recent_choch") or {}).get("direction"),
                    "chart_structure_1h_verdict": _chart_ctx.structure_1h.get("structure_verdict"),
                    "chart_structure_1h_state": _chart_ctx.structure_1h.get("current_structure"),
                    "chart_structure_1h_recent_bos_dir": (_chart_ctx.structure_1h.get("recent_bos") or {}).get("direction"),
                    "chart_structure_1h_recent_choch_dir": (_chart_ctx.structure_1h.get("recent_choch") or {}).get("direction"),
                    # Phase 9 — liquidity sweeps per timeframe
                    "chart_sweep_5m_verdict": _chart_ctx.sweeps_5m.get("sweep_verdict"),
                    "chart_sweep_5m_low_recent": _chart_ctx.sweeps_5m.get("sweep_low_recent"),
                    "chart_sweep_5m_high_recent": _chart_ctx.sweeps_5m.get("sweep_high_recent"),
                    "chart_sweep_5m_low_wick_pct": (_chart_ctx.sweeps_5m.get("sweep_low") or {}).get("wick_size_pct"),
                    "chart_sweep_5m_low_vol_ratio": (_chart_ctx.sweeps_5m.get("sweep_low") or {}).get("volume_ratio"),
                    "chart_sweep_5m_low_candles_ago": (_chart_ctx.sweeps_5m.get("sweep_low") or {}).get("candles_ago"),
                    "chart_sweep_15m_verdict": _chart_ctx.sweeps_15m.get("sweep_verdict"),
                    "chart_sweep_15m_low_recent": _chart_ctx.sweeps_15m.get("sweep_low_recent"),
                    "chart_sweep_15m_high_recent": _chart_ctx.sweeps_15m.get("sweep_high_recent"),
                    # Phase 10 — stop-cluster levels per timeframe
                    "chart_stop_cluster_5m_pct_below": _chart_ctx.stop_clusters_5m.get("nearest_stop_cluster_pct_below"),
                    "chart_stop_cluster_5m_density": _chart_ctx.stop_clusters_5m.get("stop_cluster_density"),
                    "chart_stop_cluster_5m_at_round": _chart_ctx.stop_clusters_5m.get("stop_cluster_at_round_price"),
                    "chart_stop_cluster_5m_at_pct": _chart_ctx.stop_clusters_5m.get("stop_cluster_at_pct_below"),
                    "chart_stop_cluster_5m_at_swing": _chart_ctx.stop_clusters_5m.get("stop_cluster_at_swing_low"),
                    "chart_stop_cluster_15m_pct_below": _chart_ctx.stop_clusters_15m.get("nearest_stop_cluster_pct_below"),
                    "chart_stop_cluster_15m_density": _chart_ctx.stop_clusters_15m.get("stop_cluster_density"),
                    # Phase 11 — reaccumulation pattern (5m, 12h window)
                    "chart_reaccum_verdict": _chart_ctx.reaccum_5m.get("reaccum_verdict"),
                    "chart_reaccum_drawdown_pct": _chart_ctx.reaccum_5m.get("drawdown_pct"),
                    "chart_reaccum_post_trough_candles": _chart_ctx.reaccum_5m.get("post_trough_candles"),
                    "chart_reaccum_post_trough_range_pct": _chart_ctx.reaccum_5m.get("post_trough_range_pct"),
                    "chart_reaccum_vol_return_ratio": _chart_ctx.reaccum_5m.get("vol_ratio_recent_vs_post_trough_avg"),
                }
                logger.info(
                    f"[DipScanner] CHART_READER: {token_symbol} "
                    f"score={_chart_ctx.composite_score} "
                    f"verdict={_chart_ctx.composite_verdict} "
                    f"mtf={_chart_ctx.mtf.get('alignment')} "
                    f"sr_5m_supp={_chart_ctx.sr_5m.get('at_support')} "
                    f"pattern_5m={_chart_ctx.pattern_5m.get('pattern')}"
                )
            except Exception as _e:
                logger.debug(f"[DipScanner] chart_reader error: {_e}")

            # Memecoin-specific shadow features (no new fetches; pure
            # derivations from data already in scope).

            # Lifecycle stage classifier + round-number mcap magnetism
            _lifecycle_dict: dict = {}
            try:
                from feeds.lifecycle_stage import analyze as _lc_analyze
                _lifecycle_dict = _lc_analyze(
                    mcap_usd=float(mcap or 0),
                    age_hours=pair_age_hours,
                    peak_h24_pct=float(peak_h24_6h),
                    h24_ratio_to_peak=(pc_h24 / float(peak_h24_6h)) if float(peak_h24_6h) > 0 else 1.0,
                    vol_h24_usd=float(vol_h24 or 0),
                    vol_h1_usd=float(vol_h1 or 0),
                    vol_h6_usd=float(vol_h6 or 0),
                )
            except Exception as _e:
                logger.debug(f"[DipScanner] lifecycle calc error: {_e}")

            # Trade velocity / burst features from recent_trades.
            # Always call analyze so keys are present even when recent_trades
            # is empty (analyze returns a blank-default dict in that case).
            _velocity_dict: dict = {}
            try:
                from feeds.trade_velocity import analyze as _tv_analyze
                _velocity_dict = _tv_analyze(recent_trades or [])
            except Exception as _e:
                logger.debug(f"[DipScanner] trade-velocity calc error: {_e}")

            # Liquidity-flow event tracking (stateful across cycles)
            _lp_flow_dict: dict = {}
            try:
                self._lp_flow.record(token_address, float(liq_usd or 0))
                _lp_flow_dict = self._lp_flow.analyze(
                    token_address,
                    current_liquidity_usd=float(liq_usd or 0),
                )
            except Exception as _e:
                logger.debug(f"[DipScanner] lp-flow calc error: {_e}")

            entry_meta_dict = {
                # Signal-fire wall-clock timestamp (ms). Trader.buy will
                # compute signal_to_fill_ms after on-chain confirmation.
                "signal_ts_ms": int(time.time() * 1000),
                "liquidity_usd": float(liq_usd or 0),
                "protocol": pair.get("dexId", "") or "",
                "peak_h24_6h_pct": float(peak_h24_6h),
                # Filter A — DEPRECATED but still recorded shadow-only so
                # forward data can confirm the swap to filter_real_dip_3
                # is net-positive (Filter A's PASS WR < BLOCK WR on lifetime
                # data was the trigger for replacement).
                "filter_a_verdict": _filter_a_verdict,
                "filter_a_block_reasons": _filter_a_block_reasons,
                # filter_real_dip_3 — currently enforced entry-quality gate.
                # All trade records past this point passed the gate; the
                # field exists so future filter swaps can compare against
                # this baseline.
                "filter_real_dip_3_verdict": _filter_real_dip_3_verdict,
                "filter_real_dip_3_block_reasons": _filter_real_dip_3_block_reasons,
                # filter_real_dip_5 — shadow tighter variant.
                "filter_real_dip_5_verdict": _filter_real_dip_5_verdict,
                "filter_real_dip_5_block_reasons": _filter_real_dip_5_block_reasons,
                "filter_1m_verdict": _filter_1m_verdict,
                "filter_1m_block_reasons": _filter_1m_block_reasons,
                # filter_corpse — enforced post-pump-corpse gate.
                "filter_corpse_verdict": _filter_corpse_verdict,
                "filter_corpse_block_reasons": _filter_corpse_block_reasons,
                # filter_fake_bounce — enforced 1m fake-bounce gate.
                "filter_fake_bounce_verdict": _filter_fake_bounce_verdict,
                "filter_fake_bounce_block_reasons": _filter_fake_bounce_block_reasons,
                # filter_fofar — enforced confluence gate (score>=4/5).
                "filter_fofar_verdict": _filter_fofar_verdict,
                "filter_fofar_score": _fofar_score,
                "filter_fofar_components": _fofar_components,
                "filter_fofar_block_reasons": _filter_fofar_block_reasons,
                # filter_two_pattern — enforced positive entry criterion.
                # PASS-via "A" (real dip), "B" (strength continuation),
                # "AB" (matches both), or "fail-open" (missing inputs).
                "filter_two_pattern_verdict": _filter_two_pattern_verdict,
                "filter_two_pattern_reason": _filter_two_pattern_reason,
                "filter_two_pattern_a": _tp_pattern_a,
                "filter_two_pattern_b": _tp_pattern_b,
                "h24_ratio_to_peak": (pc_h24 / peak_h24_6h) if peak_h24_6h > 0 else 1.0,
                "cycles_seen_before_buy": cycles_seen,
                "avg_trade_size_h1_usd": avg_trade_size_h1,
                "bs_h6": float(ratio_h6) if ratio_h6 != float("inf") else None,
                "bs_h1": float(ratio_h1) if ratio_h1 != float("inf") else None,
                "bs_m5": float(ratio_m5) if ratio_m5 != float("inf") else None,
                **m1_features,  # 1m candle features (or empty if fetch failed)
                **range_features,  # 5m range features (or empty if fetch failed)
                **vwap_features,  # 24h anchored VWAP (or empty if fetch failed)
                **recent_trades_features,  # last-30 trades direction (or empty)
                **sol_features,  # SOL price context (or empty if fetch failed)
                **jup_features,  # Jupiter quote asymmetry (or empty if failed)
                **tick_features,  # WS tick buffer stats (or empty if no feed)
                **trend_features,  # multi-layer trend score (or empty)
                **trajectory_features,  # pre-entry momentum trajectory (Gap 3)
                **_bot_state,  # bot-state context (concurrency, pacing, daily PnL)
                **_chart_ctx_dict,  # chart-reader shadow features (Phases 0-11)
                **_lifecycle_dict,  # lifecycle stage + mcap psych-level magnetism
                **_velocity_dict,  # trade velocity / burst detection (recent_trades-derived)
                **_lp_flow_dict,  # liquidity-flow events (LP add/remove deltas)
            }

            await self.trader.buy(
                token_address=token_address,
                token_symbol=token_symbol,
                chain_id="solana",
                override_usd=self.position_usd,
                reason=(
                    f"dip_buy: 24h={pc_h24:+.1f}% 1h={pc_h1:+.1f}% 5m={pc_m5:+.1f}% "
                    f"bs_h6={ratio_h6:.2f} bs_h1={bs_h1_str} bs_m5={bs_m5_str}"
                ),
                strategy="dip_buy",
                pair_address=pair.get("pairAddress", "") or "",
                market_cap_usd=float(mcap or 0),
                age_hours=pair_age_hours,
                volume_h1_usd=float(vol_h1 or 0),
                entry_meta=entry_meta_dict,
            )

        src_str = " ".join(f"{k}={v}" for k, v in source_counts.items() if v) or "-"
        rej_str = " ".join(
            f"{k}={c[k]}" for k in (
                "mcap_low", "mcap_high", "age", "vol", "low_turnover",
                "vol_m5_zero", "vol_h1_decay",
                "red_h24", "trend_reversal", "top_exhaustion", "no_dip", "h1_mid_dip", "m5_dip_over", "falling_knife", "mega_pump_middle",
                "seller_h1_red_m5", "seller_pump", "no_1m_reversal", "m1_top_tick", "m1_false_bounce", "top_consolidation",
                "bs_h6", "bs_h6_missing", "already_open", "loss_cooldown",
                "obs_high_cycles", "filter_peak_floor_block", "filter_real_dip_3_block",
                "filter_corpse_block", "filter_fake_bounce_block", "filter_fofar_block",
                "filter_two_pattern_block",
            ) if c[k]
        ) or "-"
        tr_log = ""
        if trend_reversal_blocked:
            tr_log = " | trend_reversal_tokens: " + ", ".join(trend_reversal_blocked)
        logger.info(
            f"[DipScanner] Cycle: fetched={c['fetched']} ({src_str}) "
            f"signals={signals} | rejects: {rej_str}{tr_log}"
        )

        # Persist h24 history once per cycle (atomic) so trend_reversal
        # filter survives process restarts.
        if self._h24_history_dirty:
            self._save_h24_history()
            self._h24_history_dirty = False

    def _load_h24_history(self) -> None:
        """
        Load persisted history; drop entries older than the 6h window.

        Backward compatible: legacy entries are 2-tuples (ts, pc_h24); new
        entries are 4-tuples (ts, pc_h24, pc_h1, pc_h6). Legacy entries are
        padded with None for h1/h6 on load, and trajectory derivation
        downstream skips None values rather than treating them as 0.
        """
        try:
            if not os.path.exists(self._h24_history_path):
                return
            with open(self._h24_history_path) as f:
                raw = json.load(f)
            cutoff = time.time() - self._h24_history_window_secs
            loaded = 0
            for addr, entries in raw.items():
                if not isinstance(entries, list):
                    continue
                fresh = []
                for entry in entries:
                    if not isinstance(entry, (list, tuple)):
                        continue
                    try:
                        if len(entry) >= 4:
                            ts = float(entry[0])
                            h24 = float(entry[1])
                            h1 = float(entry[2]) if entry[2] is not None else None
                            h6 = float(entry[3]) if entry[3] is not None else None
                        elif len(entry) == 2:
                            ts = float(entry[0])
                            h24 = float(entry[1])
                            h1 = None
                            h6 = None
                        else:
                            continue
                    except (TypeError, ValueError):
                        continue
                    if ts > cutoff:
                        fresh.append((ts, h24, h1, h6))
                if fresh:
                    self._h24_history[addr] = deque(fresh)
                    loaded += len(fresh)
            if loaded:
                logger.info(
                    f"[DipScanner] Loaded h24 history: "
                    f"{len(self._h24_history)} tokens, {loaded} samples"
                )
        except Exception as e:
            logger.warning(f"[DipScanner] Could not load h24_history.json: {e}")
            self._h24_history = {}

    def _save_h24_history(self) -> None:
        """Write history atomically (tmp + rename) in 4-tuple format."""
        try:
            os.makedirs(os.path.dirname(self._h24_history_path), exist_ok=True)
            tmp_path = self._h24_history_path + ".tmp"
            payload: Dict[str, List[list]] = {
                addr: [[ts, h24, h1, h6] for ts, h24, h1, h6 in dq]
                for addr, dq in self._h24_history.items()
                if dq  # drop empty deques
            }
            with open(tmp_path, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, self._h24_history_path)
        except Exception as e:
            logger.warning(f"[DipScanner] Could not save h24_history.json: {e}")

    async def _fetch_candidates(self) -> tuple:
        """Fetch candidate pairs from DexScreener + GeckoTerminal trending.

        GT's trending_pools response lacks the per-timeframe txns field that
        the bs_h6 filter depends on, so every GT-sourced address is batch-
        enriched through DS /tokens/ to get the full pair dict before any
        filters run.

        Returns (pairs, source_counts) where source_counts breaks down the
        origin of each token so the cycle log shows where they came from.
        """
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        pair_by_addr: dict = {}
        source_by_addr: dict = {}

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

                ds_task = asyncio.gather(*[_get(session, u) for u in urls],
                                         return_exceptions=True)
                gt_task = self.gt_client.fetch_trending_pools(pages=2)
                results, gt_pairs = await asyncio.gather(ds_task, gt_task,
                                                         return_exceptions=True)
                if isinstance(results, Exception):
                    results = []
                if isinstance(gt_pairs, Exception):
                    gt_pairs = []

                # Seed GT entries — will be overwritten by DS enrichment below
                # so the final pair dict has txns data for the bs_h6 filter.
                for p in (gt_pairs or []):
                    addr = (p.get("baseToken") or {}).get("address", "")
                    if addr and addr not in pair_by_addr:
                        pair_by_addr[addr] = p
                        source_by_addr[addr] = "gt_trending"

                # Collect stub addresses from DS boosts/profiles
                stub_addrs = []
                for res in results[:2]:
                    if isinstance(res, (list, dict)):
                        items = res if isinstance(res, list) else res.get("pairs", [])
                        for item in (items or []):
                            addr = item.get("tokenAddress") or item.get("address") or ""
                            if addr:
                                stub_addrs.append(addr)

                # Enrich stub addrs + all GT addrs via DS /tokens batch.
                # dedupe preserving first-seen order.
                to_enrich = list(dict.fromkeys(stub_addrs + list(pair_by_addr.keys())))
                if to_enrich:
                    for i in range(0, len(to_enrich), 30):
                        batch = to_enrich[i:i + 30]
                        url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                        data = await _get(session, url)
                        # DS returns one entry per pair; pick the highest-liq
                        # pair per base address.
                        best: dict = {}
                        for p in (data or {}).get("pairs", []):
                            if p.get("chainId") != _DEX_CHAIN:
                                continue
                            addr = (p.get("baseToken") or {}).get("address", "")
                            if not addr:
                                continue
                            liq = float((p.get("liquidity") or {}).get("usd") or 0)
                            cur = best.get(addr)
                            if cur is None or liq > float(
                                (cur.get("liquidity") or {}).get("usd") or 0
                            ):
                                best[addr] = p
                        for addr, p in best.items():
                            if source_by_addr.get(addr) == "gt_trending":
                                pair_by_addr[addr] = p
                                source_by_addr[addr] = "gt_enriched"
                            elif addr not in pair_by_addr:
                                pair_by_addr[addr] = p
                                source_by_addr[addr] = "ds_stub"

                # DS search results — use as fallback for addrs neither in
                # GT nor DS stub/enrichment.
                for res in results[2:]:
                    if isinstance(res, Exception) or not res:
                        continue
                    for p in (res.get("pairs") or []):
                        if p.get("chainId") != _DEX_CHAIN:
                            continue
                        addr = (p.get("baseToken") or {}).get("address", "")
                        if addr and addr not in pair_by_addr:
                            pair_by_addr[addr] = p
                            source_by_addr[addr] = "ds_search"

        except Exception as e:
            logger.error(f"[DipScanner] Fetch error: {e}")

        source_counts = {
            "ds_stub": 0, "ds_search": 0,
            "gt_trending": 0, "gt_enriched": 0,
        }
        for src in source_by_addr.values():
            source_counts[src] = source_counts.get(src, 0) + 1

        return list(pair_by_addr.values()), source_counts
