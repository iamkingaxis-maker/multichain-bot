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
# Wider pool of search keywords — sampled per-cycle to rotate the
# DS-search candidate set (2026-05-07). Prior fixed 10-term list was
# producing near-identical results every scan, contributing to the
# stale-watch fixation. Sampling 8 of ~30 terms per cycle gives ~5x
# rotation while staying within the same query budget.
_SEARCH_TERMS_POOL = [
    "sol", "bonk", "wif", "cat", "dog", "meme", "pepe", "ai", "baby", "pump",
    "trump", "moon", "frog", "elon", "shib", "doge", "based", "wojak",
    "chad", "giga", "fwog", "mog", "popcat", "brett", "cult", "spx",
    "miggles", "fartcoin", "neiro", "goat",
]
_SEARCH_TERMS_PER_CYCLE = 8
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

        # DexScreener internal-API client — used as primary OHLCV source
        # via assemble_chart_data, with GT as fallback. Bypasses GT's
        # 30/min ceiling that was capping chart_full_coverage at 6-20%.
        # Lazy-imported and lazy-initialized; if curl_cffi is missing we
        # silently degrade to GT-only.
        self.dexs_client = None
        try:
            from feeds.dexscreener_client import DexScreenerClient
            self.dexs_client = DexScreenerClient(cache_ttl=60, rate_per_min=90)
            logger.info("[DipScanner] DexScreener primary OHLCV enabled (GT fallback)")
        except Exception as _e:
            logger.info(f"[DipScanner] DexScreener client unavailable, using GT only: {_e}")

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
        # Tier-1 trackers (2026-05-04): smart-money wallet index +
        # dev-wallet baseline tracker. Both fail-open if state files
        # missing or RPC fails.
        from feeds.smart_money import SmartMoneyIndex
        from feeds.dev_wallet import DevWalletTracker
        self._smart_money = SmartMoneyIndex()
        self._dev_wallet = DevWalletTracker()
        # Jupiter slip time-series (2026-05-05) — last 10 (ts, buy_pct, sell_pct)
        # tuples per token. Used to compute slip velocity/trajectory.
        self._slip_history: Dict[str, Deque[Tuple[float, Optional[float], Optional[float]]]] = {}

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

        # Cross-token regime breadth (Tier-2 feature). Counts how many of the
        # candidates this cycle are dipping (m5<-1.5%) or rolling over (h1<0).
        # When breadth is high (>50% of scanned tokens are dipping), our entry
        # is correlated noise, not opportunity — the whole market is selling.
        _regime_n = len(pairs)
        _regime_dipping = 0
        _regime_h1_neg = 0
        for _p in pairs:
            _pc = (_p.get("priceChange") or {})
            try:
                if float(_pc.get("m5", 0) or 0) < -1.5:
                    _regime_dipping += 1
                if float(_pc.get("h1", 0) or 0) < 0:
                    _regime_h1_neg += 1
            except Exception:
                pass
        _regime_dip_breadth_pct = (
            round(_regime_dipping / _regime_n * 100, 1) if _regime_n > 0 else 0.0
        )
        _regime_h1_neg_pct = (
            round(_regime_h1_neg / _regime_n * 100, 1) if _regime_n > 0 else 0.0
        )

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

            # h1 turnover floor REMOVED 2026-05-05 — superseded by
            # filter_turn (pct_in_5m_range >= 0.5). Once filter_turn was
            # added, simulation showed dropping the turnover floor:
            #   throughput 6.9/d → 20.1/d (3x)
            #   WR 79.4% → 73.0% (still well above breakeven)
            #   $/day +$6.29 → +$10.07 (60% improvement)
            # turnover was a noisy proxy for "is something happening";
            # filter_turn captures "the bounce has actually started" more
            # precisely, making the upstream proxy counterproductive.

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
                    _chart_data = await _assemble(self.gt_client, pair_addr_for_1m, dexs_client=self.dexs_client)
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
                    # Chart-shape features over 30/60/90m windows. Logged-only,
                    # not used as a filter. Captures pump-then-bleed round-trip
                    # patterns the snapshot deltas (pc_h1/pc_h6) don't see.
                    # Uses the full 1m series fetched (~100 bars), not the
                    # last-5 slice that drives the 1m gates above.
                    try:
                        from feeds.chart_shape_features import compute_chart_shape
                        _full_1m = (_chart_data.candles_1m if _chart_data and _chart_data.candles_1m else [])
                        _shape_feats = compute_chart_shape(_full_1m)
                        m1_features.update(_shape_feats)
                    except Exception as _e:
                        logger.debug(f"[DipScanner] chart_shape error for {token_symbol}: {_e}")
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

            # ── Tier 2b: Jupiter quote asymmetry + Tier-1 slippage curve ──
            # Closest analog to "order book imbalance" on Solana AMMs.
            # Original asymmetry: buy at position size + matching sell (kept).
            # Tier-1 EXTENSION (2026-05-04): sample slippage at $500/$2000/$5000
            # to approximate orderbook depth. A token with thick book has
            # near-flat slippage curve; thin book diverges sharply at $5k.
            # Six total calls vs original two — wrapped in single ClientSession,
            # all fail-open, all parallel via asyncio.gather.
            jup_features: dict = {}
            try:
                import aiohttp as _aio
                import asyncio as _asyncio
                _SOL_MINT = "So11111111111111111111111111111111111111112"
                _JUP_URL = "https://api.jup.ag/swap/v1/quote"
                sol_price_est = sol_5m[-1].close if sol_5m else 80.0

                async def _quote(session, params):
                    try:
                        async with session.get(
                            _JUP_URL, params=params,
                            timeout=_aio.ClientTimeout(total=8)
                        ) as _r:
                            return await _r.json() if _r.status == 200 else None
                    except Exception:
                        return None

                async def _slippage_at(session, usd: float):
                    """Round-trip impact at a target USD size. Returns
                    (buy_impact_pct, sell_impact_pct) or (None, None)."""
                    sol_amount = usd / max(sol_price_est, 1.0)
                    lamports = max(int(sol_amount * 1e9), 1_000_000)
                    buy_q = await _quote(session, {
                        "inputMint": _SOL_MINT, "outputMint": token_address,
                        "amount": lamports, "slippageBps": 300,
                    })
                    if not buy_q or not buy_q.get("outAmount"):
                        return (None, None)
                    bi = float(buy_q.get("priceImpactPct") or 0) * 100
                    sell_q = await _quote(session, {
                        "inputMint": token_address, "outputMint": _SOL_MINT,
                        "amount": int(buy_q["outAmount"]), "slippageBps": 300,
                    })
                    si = float(sell_q.get("priceImpactPct") or 0) * 100 if sell_q else None
                    return (bi, si)

                async with _aio.ClientSession() as _s:
                    # Original position-size asymmetry preserved (key=quote_*)
                    base_buy_sol = self.position_usd / max(sol_price_est, 1.0)
                    base_lamports = max(int(base_buy_sol * 1e9), 1_000_000)
                    base_buy_q = await _quote(_s, {
                        "inputMint": _SOL_MINT, "outputMint": token_address,
                        "amount": base_lamports, "slippageBps": 300,
                    })
                    if base_buy_q and base_buy_q.get("outAmount"):
                        bi0 = float(base_buy_q.get("priceImpactPct") or 0) * 100
                        base_sell_q = await _quote(_s, {
                            "inputMint": token_address, "outputMint": _SOL_MINT,
                            "amount": int(base_buy_q["outAmount"]),
                            "slippageBps": 300,
                        })
                        si0 = float(base_sell_q.get("priceImpactPct") or 0) * 100 if base_sell_q else 0.0
                        jup_features = {
                            "quote_buy_impact_pct": round(bi0, 4),
                            "quote_sell_impact_pct": round(si0, 4),
                            "quote_asymmetry_pct": round(si0 - bi0, 4),
                        }
                    # Slippage curve (Tier-1) — 3 sizes in parallel
                    s500, s2k, s5k = await _asyncio.gather(
                        _slippage_at(_s, 500.0),
                        _slippage_at(_s, 2000.0),
                        _slippage_at(_s, 5000.0),
                        return_exceptions=False,
                    )
                    for label, (bi, si) in (("500", s500), ("2000", s2k), ("5000", s5k)):
                        if bi is not None:
                            jup_features[f"slip_buy_{label}_pct"] = round(bi, 4)
                        if si is not None:
                            jup_features[f"slip_sell_{label}_pct"] = round(si, 4)
                        if bi is not None and si is not None:
                            jup_features[f"slip_asym_{label}_pct"] = round(si - bi, 4)
                    # Curve steepness proxies — diff between $5k and $500 impact
                    if s500[0] is not None and s5k[0] is not None:
                        jup_features["slip_buy_curve_steepness"] = round(s5k[0] - s500[0], 4)
                    if s500[1] is not None and s5k[1] is not None:
                        jup_features["slip_sell_curve_steepness"] = round(s5k[1] - s500[1], 4)
            except Exception as _e:
                logger.debug(f"[DipScanner] Jupiter asymmetry error: {_e}")

            # ── Jupiter slip time-series (2026-05-05) ──
            # Append current quote to per-token ring buffer (last 10 samples)
            # and derive velocity (slope) and trajectory ("falling" / "rising"
            # / "flat"). Hypothesis: dips where sell-side slippage is
            # *exhausting* (slope downward) hold; dips where it's *building*
            # (slope upward) continue down. Shadow only — no enforcement.
            slip_ts_features: dict = {}
            try:
                _slip_now_buy = jup_features.get("slip_buy_5000_pct")
                _slip_now_sell = jup_features.get("slip_sell_5000_pct")
                if _slip_now_buy is not None or _slip_now_sell is not None:
                    _hist = self._slip_history.setdefault(token_address, deque(maxlen=10))
                    _hist.append((time.time(), _slip_now_buy, _slip_now_sell))
                    # Need at least 3 samples to compute slope
                    _sells = [(t, s) for (t, _b, s) in _hist if s is not None]
                    if len(_sells) >= 3:
                        # Linear-fit slope of slip_sell over time (pct/sec).
                        _t0 = _sells[0][0]
                        _xs = [t - _t0 for (t, _) in _sells]
                        _ys = [s for (_, s) in _sells]
                        _n = len(_sells)
                        _mx = sum(_xs) / _n
                        _my = sum(_ys) / _n
                        _num = sum((_xs[i] - _mx) * (_ys[i] - _my) for i in range(_n))
                        _den = sum((_xs[i] - _mx) ** 2 for i in range(_n))
                        _slope = (_num / _den) if _den > 0 else 0.0
                        # Velocity in pct-per-minute for human readability
                        _vel_per_min = _slope * 60.0
                        slip_ts_features["slip_sell_5k_velocity_pct_per_min"] = round(_vel_per_min, 4)
                        slip_ts_features["slip_sell_5k_samples"] = _n
                        # Trajectory bucket — 0.05 pct/min threshold
                        if _vel_per_min > 0.05:
                            slip_ts_features["slip_sell_5k_trajectory"] = "rising"
                        elif _vel_per_min < -0.05:
                            slip_ts_features["slip_sell_5k_trajectory"] = "falling"
                        else:
                            slip_ts_features["slip_sell_5k_trajectory"] = "flat"
                    elif _sells:
                        slip_ts_features["slip_sell_5k_samples"] = len(_sells)
                        slip_ts_features["slip_sell_5k_trajectory"] = "insufficient"
            except Exception as _e:
                logger.debug(f"[DipScanner] slip-ts calc err: {_e}")

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
            recent_trades = []
            # Try DexScreener primary (much higher rate-limit headroom; GT
            # was 100% 429-ing on this endpoint per pre-DexScreener audit).
            if self.dexs_client is not None:
                try:
                    recent_trades = await self.dexs_client.fetch_recent_trades(
                        pair_addr_for_1m, limit=30
                    )
                except Exception as _e:
                    logger.debug(f"[DipScanner] dexs recent_trades error for {token_symbol}: {_e}")
                    recent_trades = []
            # Fall back to GT only if DexScreener returned nothing.
            if not recent_trades:
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
                    f"[DipScanner] filter_peak_floor SHADOW would-block: {token_symbol} "
                    f"peak_h24_6h={float(peak_h24_6h):+.1f}% < +5%"
                )
                # ENFORCEMENT REMOVED 2026-05-04 — shadow only.
                # Part of May 1-2 filter cascade revert (user directive).

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
                    f"[DipScanner] filter_corpse SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_corpse_block_reasons)}"
                )
                # ENFORCEMENT REMOVED 2026-05-04 — shadow only.

            # Filter fake-bounce — RE-ENFORCED 2026-05-05.
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
                continue

            # Filter round-trip distribution — ENFORCED 2026-05-09.
            # Catches the "round-trip-then-distribution" loser shape from the
            # May 8-9 session post-mortem (DATA, mama, DCbBpVhrfz, KIKI):
            # token pumped into the 90-min lookback window (chg_90 >= +4%),
            # peaked 30+ minutes ago (mins_since_max >= 30), AND has fallen
            # 22%+ off that peak (drawdown_from_max <= -22%). This is the
            # exact shape where the 90m frame shows net-positive change but
            # current price is in a confirmed distribution — opposite shape
            # from a winner pullback (winners enter mid-decline with chg_90
            # negative or with a still-active peak <15 min ago).
            #
            # Methodology: chart-shape features (feeds/chart_shape_features.py)
            # added to entry_meta. Cohen's-d on n=34 retro-validated trades
            # (last 100 closed, ~24h DexScreener bar retention horizon):
            #   shape_90m_chg_pct: W mean -13.5%, L mean +44.1% (diff +57.6)
            #   shape_90m_pump_bleed_score: W +46.2, L -7.5 (diff +53.7)
            #
            # Lifetime validation on 34 retro: BLOCK n=4, ALL LOSSES (0W/4L),
            # sum -$13.33 (delta vs no-filter +$13.33). Winners on the same
            # cohort that share round-trip features pass cleanly thanks to
            # the mins_since_max>=30 + dd<=-22 conjunction:
            #   - maxxing-1 (+$2.30, dd=-21.2 just under threshold)
            #   - 21rKrtBzib (+$1.05, max@9min — active runner)
            #   - xNGegLW3dg (+$2.60, max@10min — active runner)
            #
            # Fail-open if shape features missing — chart_shape_features
            # returns {} when 1m bar series is too short (<22 bars), so all
            # three keys can be None on tokens with thin history.
            _shape_chg90 = m1_features.get("shape_90m_chg_pct")
            _shape_max_age90 = m1_features.get("shape_90m_mins_since_max")
            _shape_dd90 = m1_features.get("shape_90m_drawdown_from_max_pct")
            _filter_round_trip_block_reasons: list = []
            if (
                _shape_chg90 is not None
                and _shape_max_age90 is not None
                and _shape_dd90 is not None
                and _shape_chg90 >= 4.0
                and _shape_max_age90 >= 30
                and _shape_dd90 <= -22.0
            ):
                _filter_round_trip_block_reasons.append(
                    f"shape_90m_chg={_shape_chg90:+.1f}%>=4 AND "
                    f"max@{int(_shape_max_age90)}m>=30 AND "
                    f"dd_from_max={_shape_dd90:.1f}%<=-22 (round-trip distribution)"
                )
            _filter_round_trip_verdict = "BLOCK" if _filter_round_trip_block_reasons else "PASS"
            c[f"filter_round_trip_{_filter_round_trip_verdict.lower()}"] = c.get(
                f"filter_round_trip_{_filter_round_trip_verdict.lower()}", 0
            ) + 1
            if _filter_round_trip_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_round_trip: {token_symbol} "
                    f"reasons={','.join(_filter_round_trip_block_reasons)}"
                )
                continue

            # Filter weak-bounce v2 — ENFORCED 2026-05-09.
            # Compound rule: 5m candle body/range < 0.20 (weak commitment)
            # AND 1m_volume_spike < 0.50 (no real demand kicking in). Fires
            # only when both signals agree the bounce is hollow.
            #
            # Mechanism: a thin 5m body inside a wide wick means the recent
            # candle was indecisive — not a real reversal commit. Combined
            # with 1m volume below half the trailing average means demand
            # isn't actually showing up. This is the same family as
            # filter_fake_bounce but body-shape-led instead of cum-3min-led.
            #
            # Methodology (compound search vs the alone-shadow):
            #   - Baseline weak_bounce alone: 35 fires, 17W/18L, +$17.05 net
            #   - + 1m_volume_spike < 0.50: 15 fires, 4W/11L, +$22.12 net
            #   - WR of fires drops 48.6% → 26.7% (-22pp vs baseline)
            #   - Save:cut ratio 1.53 → 4.21 (nearly 3× more selective)
            #   - 78% of original winner-cuts eliminated, 59% of saves kept
            #
            # Lifetime caveat: in-sample on the 180-trade window since
            # weak_bounce shadow deployed 2026-05-05 (the only data we have).
            # Forward fires will be the held-out validation. Filter is
            # intentionally narrow (~3.75 fires/day on lifetime).
            #
            # Fail-open on either feature missing.
            _wbv2_body_ratio: float | None = None
            try:
                _wbv2_c5 = (_chart_data.candles_5m
                            if _chart_data and _chart_data.candles_5m else None)
                if _wbv2_c5:
                    _wbv2_last = _wbv2_c5[-1]
                    _wbv2_body = abs(_wbv2_last.close - _wbv2_last.open)
                    _wbv2_rng = _wbv2_last.high - _wbv2_last.low
                    if _wbv2_rng > 0:
                        _wbv2_body_ratio = _wbv2_body / _wbv2_rng
            except Exception as _e:
                logger.debug(f"[DipScanner] wbv2 calc err: {_e}")
            _wbv2_vol_spike = m1_features.get("1m_volume_spike")
            _filter_weak_bounce_v2_block_reasons: list = []
            if (
                _wbv2_body_ratio is not None
                and _wbv2_vol_spike is not None
                and _wbv2_body_ratio < 0.20
                and _wbv2_vol_spike < 0.50
            ):
                _filter_weak_bounce_v2_block_reasons.append(
                    f"body/range={_wbv2_body_ratio:.2f}<0.20 AND "
                    f"1m_vol_spike={_wbv2_vol_spike:.2f}<0.50 "
                    f"(weak bounce on dead 1m volume)"
                )
            _filter_weak_bounce_v2_verdict = (
                "BLOCK" if _filter_weak_bounce_v2_block_reasons else "PASS"
            )
            c[f"filter_weak_bounce_v2_{_filter_weak_bounce_v2_verdict.lower()}"] = c.get(
                f"filter_weak_bounce_v2_{_filter_weak_bounce_v2_verdict.lower()}", 0
            ) + 1
            if _filter_weak_bounce_v2_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_weak_bounce_v2: {token_symbol} "
                    f"reasons={','.join(_filter_weak_bounce_v2_block_reasons)}"
                )
                continue

            # ── filter_turn_confirmation — DOWNGRADED TO SHADOW 2026-05-05 PM ─
            # Originally enforced (forward sim showed +$6.29/day vs -$2.91/day),
            # but the live forward test (5h, n=121 phantom trades on the
            # trending feed) showed Z_truly_unfiltered at +$0.17/trade and
            # B_with_filter_turn at -$0.11/trade — filter_turn was a NET
            # NEGATIVE on that out-of-sample slice. Downgraded to shadow to
            # collect more forward data before deciding to re-enforce or kill.
            #
            # Original rationale (preserved for context): require
            # pct_in_5m_range >= 0.5 — entry only when the most recent close
            # has reached the upper half of the 5m candle's range. Below 0.5
            # = catching a falling knife. Cohen's d=+0.91 on the post-Apr-30
            # fast-mover survivor cohort (winners 0.68 vs losers 0.33).
            _filter_turn_block_reasons: list = []
            if pct_in_5m_range < 0.5:
                _filter_turn_block_reasons.append(
                    f"pct_in_5m_range={pct_in_5m_range:.3f}<0.5 (catching knife)"
                )
            _filter_turn_verdict = "BLOCK" if _filter_turn_block_reasons else "PASS"
            c[f"filter_turn_{_filter_turn_verdict.lower()}"] = c.get(
                f"filter_turn_{_filter_turn_verdict.lower()}", 0
            ) + 1
            if _filter_turn_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_turn SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_turn_block_reasons)}"
                )

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
                    f"[DipScanner] filter_real_dip_3 SHADOW would-block: {token_symbol} "
                    f"5m={pc_m5:+.2f}% 1h={pc_h1:+.2f}% "
                    f"reasons={','.join(_filter_real_dip_3_block_reasons)}"
                )
                # ENFORCEMENT REMOVED 2026-05-04 — shadow only.
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
                    f"[DipScanner] filter_fofar SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_fofar_block_reasons)}"
                )
                # ENFORCEMENT REMOVED 2026-05-04 — shadow only.

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
                    f"[DipScanner] filter_two_pattern SHADOW would-block: {token_symbol} "
                    f"reason={_filter_two_pattern_reason}"
                )
                # ENFORCEMENT REMOVED 2026-05-04 — shadow only.

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

            # Filter vp_poc_above — ENFORCED 2026-05-08, retuned 2026-05-08 PM (B3).
            # Catches the "extreme above POC on dead volume" pattern: blocks when
            # chart_vp_poc_distance_pct > 20 AND 1m_volume_spike < 1.0. Only fires
            # on entries FAR above the volume profile point of control AND with
            # 1m volume below trailing average. These are unambiguous post-pump
            # distribution chases — the heaviest volume traded WAY below current
            # price, and there's no fresh buying confirming the move.
            #
            # Original v2 (vp>0 AND vs<1.0) over-blocked in production: forward
            # rate was 100% on the 12 recent live signals (vs 21% retroactive
            # estimate). Live trending tokens are systematically more "above POC"
            # than the historical closed-trade cohort because POC lags during
            # active pumps. Tightening the vp_poc threshold from >0 to >20
            # restores ~33% of buy volume while preserving the unambiguous
            # chase-blocking behavior.
            #
            # Methodology: Cohen's d feature scan showed vp_poc as the most
            # discriminating chart-derived feature (winners median -6.0% /
            # losers median +3.2%). The vp_poc>20 threshold catches all the
            # extreme cases (HANTA +151%, ANTIHANTA +24%, AMERICA +75%, DCB
            # +176/+209%, AALIEN +22%) while letting through borderline pumps
            # that may be legitimate (HENTAI +9.8%, CHUD +1.0%, NOTHING +5.1%).
            #
            # Lifetime validation (held-out, n=1009):
            #   v1 vp>0:                   blocks 254, swing +$112.34
            #   v2 vp>0  AND vs<1.0:       blocks 223, swing +$169.59 (overshot
            #                              forward — 100% of recent live blocks)
            #   B3 vp>20 AND vs<1.0:       blocks  98, swing  +$96.89, big_killed=0,
            #                              forward block rate 67% on recent 12
            # B3 trades $73 of validated lifetime swing for restored forward
            # buy volume. The blocks B3 still catches are unambiguous chases.
            #
            # Fail-open if chart_vp_poc_distance_pct missing (chart_reader
            # exception or no volume profile data) OR if 1m_volume_spike missing
            # (1m fetch failed).
            _vp_poc_dist = _chart_ctx_dict.get("chart_vp_poc_distance_pct")
            _vp_poc_vs = m1_features.get("1m_volume_spike")
            _filter_vp_poc_block_reasons: list = []
            if (
                _vp_poc_dist is not None and _vp_poc_dist > 20
                and _vp_poc_vs is not None and _vp_poc_vs < 1.0
            ):
                _filter_vp_poc_block_reasons.append(
                    f"chart_vp_poc_distance_pct={_vp_poc_dist:+.1f}%>20 AND "
                    f"1m_volume_spike={_vp_poc_vs:.2f}<1.0 "
                    f"(extreme above POC on dead volume = clear chase)"
                )
            _filter_vp_poc_verdict = "BLOCK" if _filter_vp_poc_block_reasons else "PASS"
            c[f"filter_vp_poc_{_filter_vp_poc_verdict.lower()}"] = c.get(
                f"filter_vp_poc_{_filter_vp_poc_verdict.lower()}", 0
            ) + 1
            if _filter_vp_poc_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_vp_poc: {token_symbol} "
                    f"reasons={','.join(_filter_vp_poc_block_reasons)}"
                )
                continue

            # ── Note: filter_combo enforcement moved to core/trader.py ──
            # The Pareto-best 50%-block combo from filter_combo_pareto.py
            # requires lp_locked_pct, which is only fetched post-rugcheck
            # in trader.buy. See `filter_combo_v2` block in core/trader.py.

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

            # Order-size distribution + buyer uniqueness / wash detection +
            # buyer profile (whale-present / recurring buyer count).
            # Maker-address-derived fields populate only when DexScreener
            # was the trade source (GT fallback strips maker).
            _trade_log_dict: dict = {}
            try:
                from feeds.trade_log_features import analyze as _tlf_analyze
                _trade_log_dict = _tlf_analyze(recent_trades or [])
            except Exception as _e:
                logger.debug(f"[DipScanner] trade-log-features calc error: {_e}")

            # Bonding-curve graduation status — memecoin-specific lifecycle
            # marker that lifecycle_stage doesn't fully capture. PumpSwap
            # tokens within 24h of graduation behave distinctively (initial
            # pump-fade pattern, then drawdown). Categorical:
            #   pre_graduation       — still on pump.fun bonding curve
            #   just_graduated       — on PumpSwap and age<24h
            #   post_graduated_aged  — on PumpSwap and age>=24h
            #   established          — any other AMM (Raydium, Orca, etc)
            _graduation_dict: dict = {}
            try:
                _ds_dex = str(pair.get("dexId", "") or "").lower()
                if "pumpfun" in _ds_dex and "swap" not in _ds_dex:
                    _grad_status = "pre_graduation"
                elif _ds_dex == "pumpswap" and pair_age_hours < 24.0:
                    _grad_status = "just_graduated"
                elif _ds_dex == "pumpswap" and pair_age_hours >= 24.0:
                    _grad_status = "post_graduated_aged"
                else:
                    _grad_status = "established"
                _graduation_dict = {
                    "graduation_status": _grad_status,
                    "graduation_dex_id": _ds_dex or "?",
                }
            except Exception as _e:
                logger.debug(f"[DipScanner] graduation calc error: {_e}")
                _graduation_dict = {"graduation_status": "?", "graduation_dex_id": "?"}

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

            # ── Tier-2 features (2026-05-04) — instrumentation only ──
            # All shadow; no enforcement until forward-validated by re-running
            # the exhaustive combo search with these features in the library.
            # Each compute_* fail-opens (returns {} on bad input). The 5m/15m
            # candle series come from the same _chart_data fetched at top of
            # this iteration — zero extra GT calls.
            _tier2_features: dict = {}
            try:
                from feeds.tier2_features import (
                    compute_anchored_vwap_1h,
                    compute_pct_off_peak,
                    compute_higher_low_5m,
                    compute_rsi_bb,
                    compute_bundle_v2,
                    compute_trade_size_shift,
                )
                _cs5_full = (_chart_data.candles_5m if _chart_data and _chart_data.candles_5m else [])
                _cs15_full = (_chart_data.candles_15m if _chart_data and _chart_data.candles_15m else [])
                _cur_price = _cs5_full[-1].close if _cs5_full else 0.0
                # 1. Anchored VWAP — 1h window
                _tier2_features.update(
                    compute_anchored_vwap_1h(_cs15_full, _cur_price)
                )
                # 2. pct_off_peak + minutes_since_peak
                _tspk = trajectory_features.get("time_since_h24_peak_secs") if trajectory_features else None
                _tier2_features.update(
                    compute_pct_off_peak(float(pc_h24 or 0), float(peak_h24_6h or 0), _tspk)
                )
                # 3. Higher-low confirmation (uses full 5m series, not just 12)
                _tier2_features.update(compute_higher_low_5m(_cs5_full))
                # 4. RSI(14) + BB(20,2) on 5m and 15m
                _tier2_features.update(compute_rsi_bb(_cs5_full, _cs15_full))
                # 5. Bundle-v2 detector (top-10 buyer cluster timing)
                _tier2_features.update(
                    compute_bundle_v2(recent_trades or [], pair_age_hours)
                )
                # 6. Trade-size distribution shift (last-60s vs prior-60s)
                _tier2_features.update(
                    compute_trade_size_shift(recent_trades or [])
                )
            except Exception as _e:
                logger.debug(f"[DipScanner] tier2 features error: {_e}")

            # 7. Cross-token regime breadth (computed once per scan cycle above)
            _tier2_features["regime_dip_breadth_pct"] = _regime_dip_breadth_pct
            _tier2_features["regime_h1_neg_pct"] = _regime_h1_neg_pct
            _tier2_features["regime_n_tokens_scanned"] = _regime_n

            # ── Tier-3 features (2026-05-04) — narrow but bundleable ──
            # Support touches, wick:body ratios, freq derivative, net flow
            # windows, hours_since_graduation. All computed from data
            # already fetched. Each function fail-opens.
            _tier3_features: dict = {}
            try:
                from feeds.tier3_features import (
                    compute_support_touches, compute_wick_body_ratios,
                    compute_freq_derivative, compute_net_flow_windows,
                    compute_hours_since_grad,
                )
                _cs5_full2 = (_chart_data.candles_5m if _chart_data and _chart_data.candles_5m else [])
                _tier3_features.update(compute_support_touches(_cs5_full2))
                _tier3_features.update(compute_wick_body_ratios(_cs5_full2))
                _tier3_features.update(compute_freq_derivative(recent_trades or []))
                _tier3_features.update(compute_net_flow_windows(recent_trades or []))
                _grad_status = (_graduation_dict or {}).get("graduation_status", "?")
                _tier3_features.update(
                    compute_hours_since_grad(_grad_status, pair_age_hours)
                )
            except Exception as _e:
                logger.debug(f"[DipScanner] tier3 features error: {_e}")

            # ── Tier-1 features (2026-05-04) ──
            # Smart-money wallet detection (cheap lookup against pre-built index)
            # + top-N maker capture (data feedstock for index rebuild) +
            # dev-wallet supply tracking via Solana RPC.
            _tier1_features: dict = {}
            try:
                from feeds.smart_money import extract_top_makers
                _tier1_features.update(
                    self._smart_money.score_recent_trades(recent_trades or [])
                )
                _tier1_features.update(extract_top_makers(recent_trades or []))
            except Exception as _e:
                logger.debug(f"[DipScanner] smart-money error: {_e}")
            # Dev wallet (async RPC). Wrapped in its own try so RPC stalls
            # don't kill the rest of tier-1.
            try:
                _dev_feats = await self._dev_wallet.get_features(token_address)
                _tier1_features.update(_dev_feats)
            except Exception as _e:
                logger.debug(f"[DipScanner] dev-wallet error: {_e}")

            # ── 4 SHADOW filters added 2026-05-05 ──────────────────────
            # All shadow only — no `continue`. Collecting forward data on
            # 4 different angles. Each fail-opens if its feature(s) absent.
            #
            # 1) filter_weak_bounce — body_5m/range_5m < 0.20.
            #    Hypothesis: a bounce with a tiny green body inside a
            #    wide wick range = weak commitment, likely fade.
            # 2) filter_slip_asym — sell-side liquidity hostile relative
            #    to buy. slip_sell_5000_pct > 8% OR ratio>1.5x slip_buy.
            # 3) filter_regime_panic — broad market bleeding.
            #    regime_h1_neg_pct > 70 = >70% of scanned tokens red on h1.
            # 4) filter_dev_dumping — dev_pct_remaining < 50.
            #    Dev has dumped >half their bag pre-entry → exit risk.

            # 1) Weak-bounce (5m body/range)
            _filter_weak_bounce_block_reasons: list = []
            _filter_weak_bounce_body_over_range: float | None = None
            try:
                _last5 = (_cs5_full[-1] if _cs5_full else None)
                if _last5 is not None:
                    _body = abs(_last5.close - _last5.open)
                    _rng = _last5.high - _last5.low
                    if _rng > 0:
                        _br = _body / _rng
                        _filter_weak_bounce_body_over_range = _br
                        if _br < 0.20:
                            _filter_weak_bounce_block_reasons.append(
                                f"body/range={_br:.2f}<0.20 (weak commitment in 5m candle)"
                            )
            except Exception as _e:
                logger.debug(f"[DipScanner] weak-bounce calc err: {_e}")
            _filter_weak_bounce_verdict = "BLOCK" if _filter_weak_bounce_block_reasons else "PASS"
            c[f"filter_weak_bounce_{_filter_weak_bounce_verdict.lower()}"] = c.get(
                f"filter_weak_bounce_{_filter_weak_bounce_verdict.lower()}", 0
            ) + 1
            if _filter_weak_bounce_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_weak_bounce SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_weak_bounce_block_reasons)}"
                )

            # 2) Slip-asymmetry (Jupiter quote)
            _filter_slip_asym_block_reasons: list = []
            _slip_buy_5k = jup_features.get("slip_buy_5000_pct")
            _slip_sell_5k = jup_features.get("slip_sell_5000_pct")
            if _slip_buy_5k is not None and _slip_sell_5k is not None:
                if _slip_sell_5k > 8.0:
                    _filter_slip_asym_block_reasons.append(
                        f"slip_sell_5k={_slip_sell_5k:.2f}%>8% (exit liquidity hostile)"
                    )
                if _slip_buy_5k > 0 and (_slip_sell_5k / _slip_buy_5k) > 1.5:
                    _filter_slip_asym_block_reasons.append(
                        f"slip_sell/slip_buy={_slip_sell_5k/_slip_buy_5k:.2f}>1.5 (asymmetric)"
                    )
            _filter_slip_asym_verdict = "BLOCK" if _filter_slip_asym_block_reasons else "PASS"
            c[f"filter_slip_asym_{_filter_slip_asym_verdict.lower()}"] = c.get(
                f"filter_slip_asym_{_filter_slip_asym_verdict.lower()}", 0
            ) + 1
            if _filter_slip_asym_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_slip_asym SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_slip_asym_block_reasons)}"
                )

            # 3) Regime-panic (cross-token breadth)
            _filter_regime_panic_block_reasons: list = []
            if _regime_h1_neg_pct is not None and _regime_h1_neg_pct > 70:
                _filter_regime_panic_block_reasons.append(
                    f"regime_h1_neg={_regime_h1_neg_pct:.1f}%>70 (broad market bleeding)"
                )
            _filter_regime_panic_verdict = "BLOCK" if _filter_regime_panic_block_reasons else "PASS"
            c[f"filter_regime_panic_{_filter_regime_panic_verdict.lower()}"] = c.get(
                f"filter_regime_panic_{_filter_regime_panic_verdict.lower()}", 0
            ) + 1
            if _filter_regime_panic_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_regime_panic SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_regime_panic_block_reasons)}"
                )

            # 4) Dev-dumping (creator wallet)
            _filter_dev_dumping_block_reasons: list = []
            _dev_pct = _tier1_features.get("dev_pct_remaining")
            if _dev_pct is not None and _dev_pct < 50.0:
                _filter_dev_dumping_block_reasons.append(
                    f"dev_pct_remaining={_dev_pct:.1f}%<50 (creator dumped >half pre-entry)"
                )
            _filter_dev_dumping_verdict = "BLOCK" if _filter_dev_dumping_block_reasons else "PASS"
            c[f"filter_dev_dumping_{_filter_dev_dumping_verdict.lower()}"] = c.get(
                f"filter_dev_dumping_{_filter_dev_dumping_verdict.lower()}", 0
            ) + 1
            if _filter_dev_dumping_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_dev_dumping SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_dev_dumping_block_reasons)}"
                )

            # ── 3 SHADOW filters from regret analysis (2026-05-05 PM) ─────────
            # Surfaced by retroactive brute-force search across 877 closed
            # paired trades (held-out 70/30 train/test split). Each was the
            # most robust threshold for its feature on the SHAP-ranked top-20
            # list, and each is non-redundant with existing filters.

            # 5) bs_m5 — buy/sell ratio on 5m. Block when sellers dominate.
            # Train n=361, lift +$884. Test n=229, lift +$53.
            _filter_bs_m5_low_block_reasons: list = []
            try:
                _bs_m5_val = float(ratio_m5) if ratio_m5 != float("inf") else None
                if _bs_m5_val is not None and _bs_m5_val < 1.40:
                    _filter_bs_m5_low_block_reasons.append(
                        f"bs_m5={_bs_m5_val:.2f}<1.40 (sellers dominating 5m order flow)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] bs_m5_low calc err: {_e}")
            _filter_bs_m5_low_verdict = "BLOCK" if _filter_bs_m5_low_block_reasons else "PASS"
            c[f"filter_bs_m5_low_{_filter_bs_m5_low_verdict.lower()}"] = c.get(
                f"filter_bs_m5_low_{_filter_bs_m5_low_verdict.lower()}", 0
            ) + 1
            if _filter_bs_m5_low_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_bs_m5_low SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_bs_m5_low_block_reasons)}"
                )

            # 6) avg_trade_size_h1 — block tokens with big trades preceding
            # the dip ($80+ avg in last hour = whales selling in size).
            # Train n=425, lift +$802. Test n=267, lift +$45.
            _filter_big_trade_size_block_reasons: list = []
            try:
                if avg_trade_size_h1 is not None and float(avg_trade_size_h1) > 80.0:
                    _filter_big_trade_size_block_reasons.append(
                        f"avg_trade_size_h1=${float(avg_trade_size_h1):.0f}>$80 (whale-sized trades preceding dip)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] big_trade_size calc err: {_e}")
            _filter_big_trade_size_verdict = "BLOCK" if _filter_big_trade_size_block_reasons else "PASS"
            c[f"filter_big_trade_size_{_filter_big_trade_size_verdict.lower()}"] = c.get(
                f"filter_big_trade_size_{_filter_big_trade_size_verdict.lower()}", 0
            ) + 1
            if _filter_big_trade_size_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_big_trade_size SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_big_trade_size_block_reasons)}"
                )

            # 7) cycles_seen_before_buy — block stale watches (>15 cycles).
            # Train n=230, lift +$721. Test n=164, lift +$74.
            # ENFORCED 2026-05-07: live signal-eval funnel was 100% stale
            # tokens (median cycles_seen=150) — same handful re-traded for
            # hours. Promoting to enforced should clear the funnel for
            # fresh candidates and let parallel triggers reach bars they
            # never see today.
            # DEMOTED to SHADOW 2026-05-07 PM: re-derived against full lifetime
            # (n=1357) shows the 15 cutoff is too aggressive — bucket [16-30]
            # is +$0.82/trade (sum +$148), bucket [101-200] is +$2.36/trade
            # (sum +$198). Real damage is in [31-100] (-$3+/trade). Recent
            # half data shows near-zero lift overall ($0.21/trade). Funnel-
            # clearing benefit also gone now that Axiom auth refresh is fixed
            # and fresh tokens flow normally. Shadow to gather forward data
            # before deciding final shape (raise threshold, bucketed rule, kill).
            _filter_stale_watch_block_reasons: list = []
            try:
                if cycles_seen is not None and int(cycles_seen) > 15:
                    _filter_stale_watch_block_reasons.append(
                        f"cycles_seen={int(cycles_seen)}>15 (stale watch — fresh dips beat stale ones)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] stale_watch calc err: {_e}")
            _filter_stale_watch_verdict = "BLOCK" if _filter_stale_watch_block_reasons else "PASS"
            c[f"filter_stale_watch_{_filter_stale_watch_verdict.lower()}"] = c.get(
                f"filter_stale_watch_{_filter_stale_watch_verdict.lower()}", 0
            ) + 1
            if _filter_stale_watch_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_stale_watch SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_stale_watch_block_reasons)}"
                )

            # ── filter_confirmation_candle — SHADOW 2026-05-05 PM ─────────────
            # Timing fix: require POSITIVE confirmation on the entry 1m candle
            # before firing. Two losses on 2026-05-05 night-time fired right
            # before tokens pumped — classic "we entered at the wrong time"
            # pattern (Loss 1: 5m looked OK but 1m bounce was on dead volume;
            # Loss 2: knife-catch at pct_in_5m_range=0.135).
            #
            # filter_fake_bounce blocks the OPPOSITE pattern (1m green pulse
            # on dead volume — air, not real buying). This filter requires
            # the POSITIVE form: 1m_last_close >= +0.3% (real green close,
            # not dead-flat) AND 1m_volume_spike >= 1.0 (real buying volume,
            # not air).
            #
            # Fail-open if 1m features missing.
            _filter_confirm_block_reasons: list = []
            _confirm_lcp = m1_features.get("1m_last_close_pct")
            _confirm_vs = m1_features.get("1m_volume_spike")
            if _confirm_lcp is not None and _confirm_lcp < 0.3:
                _filter_confirm_block_reasons.append(
                    f"1m_last_close={_confirm_lcp:+.2f}%<0.3 (no real green confirmation)"
                )
            if _confirm_vs is not None and _confirm_vs < 1.0:
                _filter_confirm_block_reasons.append(
                    f"1m_vol_spike={_confirm_vs:.2f}<1.0 (bounce on weak volume)"
                )
            _filter_confirm_verdict = "BLOCK" if _filter_confirm_block_reasons else "PASS"
            c[f"filter_confirmation_candle_{_filter_confirm_verdict.lower()}"] = c.get(
                f"filter_confirmation_candle_{_filter_confirm_verdict.lower()}", 0
            ) + 1
            if _filter_confirm_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_confirmation_candle SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_confirm_block_reasons)}"
                )

            # ── filter_clean_break — ENFORCED 2026-05-06 ──────────────────────
            # User-spotted pattern from GME/SELLOR postmortems: visually clean
            # "downtrend break" reversals on 1m — lower-lows breaking with the
            # first green candle after a series of red. Maps to:
            #   - 1m_consec_red == 0 (current candle green/flat)
            #   - 1m_red_count_5 >= 3 (recent 5-candle window red-dominated)
            #   - 1m_last_close_pct > 0 (positive confirmation)
            #
            # Held-out 70/30 validation: TRAIN +5pp lift, TEST +13pp lift
            # (lift INCREASES on held-out — opposite of overfitting). TEST
            # PASS cohort: n=54, 68% WR, +$0.15/trade vs strategy avg
            # -$0.19/trade. If filter had been live for last ~2 days, that
            # period flips from -$48 to +$8.
            #
            # Aggressive: blocks ~86% of candidates. Throughput still
            # ~25 entries/day in test. Fail-open if 1m features missing.
            _clean_consec = m1_features.get("1m_consec_red")
            _clean_red5 = m1_features.get("1m_red_count_5")
            _clean_lcp = m1_features.get("1m_last_close_pct")
            _filter_clean_break_block_reasons: list = []
            if (
                _clean_consec is not None
                and _clean_red5 is not None
                and _clean_lcp is not None
            ):
                _is_clean = (
                    _clean_consec == 0
                    and _clean_red5 >= 3
                    and _clean_lcp > 0
                )
                if not _is_clean:
                    _filter_clean_break_block_reasons.append(
                        f"consec_red={_clean_consec},red5={_clean_red5},"
                        f"last_close={_clean_lcp:+.2f}% (not first-green-after-red)"
                    )
            _filter_clean_break_verdict = (
                "BLOCK" if _filter_clean_break_block_reasons else "PASS"
            )
            c[f"filter_clean_break_{_filter_clean_break_verdict.lower()}"] = c.get(
                f"filter_clean_break_{_filter_clean_break_verdict.lower()}", 0
            ) + 1

            # ── trigger_4combo — PARALLEL ENTRY TRIGGER 2026-05-06 PM ─────────
            # Fires INDEPENDENTLY of clean_break when all 4 conditions match:
            #   1. macro30 in [-15%, -3%] (moderate pullback zone)
            #   2. current vol > 1.5x avg of last 5 (real buying)
            #   3. close > max high of last 5 (5-bar breakout)
            #   4. higher_low (current low > previous low)
            #
            # Different shape from clean_break (first green after sustained
            # red) — this catches "pullback + breakout + basing" reversals
            # that don't always coincide with green-after-red.
            #
            # Validator results (scripts/validate_trigger.py):
            #   - Sim trigger_only marginal: n=304 WR=60.8% avg=+0.93%/trade
            #   - Retro on 8 bot-traded pairs: n=22 NEW marginal entries,
            #     55.6% WR, 5 missed winners (GME 14:54 +6%, GME 17:58 +13%,
            #     Apple 14:12/15:56/17:25 +6% each).
            #
            # Bot enters if EITHER clean_break PASS OR 4-combo match. Both
            # paths still pass through filter_double_bear and
            # filter_seller_dominant downstream.
            _trigger_4combo_match = False
            _trigger_4combo_reasons: list = []
            try:
                _t4_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_t4_cs) >= 31 and _t4_cs[-1].open > 0:
                    _t4_cur = _t4_cs[-1]
                    _t4_30ago = _t4_cs[-31]
                    if _t4_30ago.close > 0:
                        _t4_m30 = (_t4_cur.close / _t4_30ago.close - 1) * 100
                        _t4_in_zone = -15 <= _t4_m30 <= -3
                        _t4_prior_vol = [b.volume for b in _t4_cs[-6:-1]]
                        _t4_avg_v = (sum(_t4_prior_vol) / len(_t4_prior_vol)
                                     if _t4_prior_vol else 0)
                        _t4_vol_ok = _t4_avg_v > 0 and _t4_cur.volume / _t4_avg_v > 1.5
                        _t4_prior_high = (max(b.high for b in _t4_cs[-6:-1])
                                          if len(_t4_cs) >= 6 else 0)
                        _t4_breakout = _t4_cur.close > _t4_prior_high
                        _t4_higher_low = (_t4_cur.low > _t4_cs[-2].low
                                          if len(_t4_cs) >= 2 else False)
                        if _t4_in_zone and _t4_vol_ok and _t4_breakout and _t4_higher_low:
                            _trigger_4combo_match = True
                            _trigger_4combo_reasons.append(
                                f"m30={_t4_m30:+.1f}% in_zone, "
                                f"vol_spike={_t4_cur.volume / _t4_avg_v:.2f}x, "
                                f"breakout (close>{_t4_prior_high:.6f}), "
                                f"higher_low"
                            )
            except Exception as _e:
                logger.debug(f"[DipScanner] 4combo calc err: {_e}")

            # ── trigger_quiet_pop_breakout — PARALLEL ENTRY TRIGGER 2026-05-06 PM ─
            # Fires INDEPENDENTLY when:
            #   1. Last 3 bars all had small vol (<0.8x avg of prior 10) — quiet
            #   2. Current vol > 2x avg of prior 10 — pop
            #   3. Current close > max high of last 5 bars — breakout
            #   4. Green close
            #
            # Mechanism: consolidation/accumulation phase ends with breakout on
            # explosive volume. Different from clean_break (green-after-red),
            # 4-combo (pullback+vol+breakout+HL), or capitulation patterns.
            #
            # Validator (scripts/validate_trigger.py):
            #   - Sim trigger_only marginal: avg=+1.02%/trade
            #   - Retro on 8 bot pairs: n=24 NEW, 69% WR, +1.93%/trade, sum +$46
            #     (best retro signal of any trigger tested). Catches GME 15:42
            #     +13%, GME 18:21 +13%, Apple 21:12 +13%, etc.
            _trigger_quietpop_match = False
            _trigger_quietpop_reasons: list = []
            try:
                _qp_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_qp_cs) >= 14 and _qp_cs[-1].open > 0:
                    _qp_cur = _qp_cs[-1]
                    if _qp_cur.close > _qp_cur.open:  # green close
                        # avg vol over prior 10 (excluding last 3 quiet bars and current)
                        _qp_avg10 = sum(b.volume for b in _qp_cs[-13:-3]) / 10
                        if _qp_avg10 > 0:
                            _qp_last3_quiet = all(
                                b.volume < _qp_avg10 * 0.8
                                for b in _qp_cs[-4:-1]
                            )
                            _qp_pop = _qp_cur.volume / _qp_avg10 > 2.0
                            _qp_prior5_high = (max(b.high for b in _qp_cs[-6:-1])
                                               if len(_qp_cs) >= 6 else 0)
                            _qp_breakout = _qp_cur.close > _qp_prior5_high
                            if _qp_last3_quiet and _qp_pop and _qp_breakout:
                                _trigger_quietpop_match = True
                                _trigger_quietpop_reasons.append(
                                    f"3-quiet (last3<{_qp_avg10*0.8:.0f}), "
                                    f"vol_pop={_qp_cur.volume / _qp_avg10:.2f}x, "
                                    f"breakout (close>{_qp_prior5_high:.6f})"
                                )
            except Exception as _e:
                logger.debug(f"[DipScanner] quietpop calc err: {_e}")

            # ── deep_breakout_volume parallel trigger — ENFORCED 2026-05-06 ─────
            # Fires when ALL THREE:
            #   1. Current close > max high of last 10 bars (deep breakout)
            #   2. Current vol > 1.5x avg of last 5 bars
            #   3. Green close
            #
            # Highest-volume parallel trigger (not selectivity-bound). 10-bar
            # breakout is a stronger trend-break signal than 5-bar, which makes
            # this less prone to repeat-fires during slow bleeds despite the
            # looser volume threshold.
            #
            # Validator (scripts/validate_trigger.py):
            #   - Sim trigger_only marginal: avg=+0.53%/trade
            #   - Retro on bot pairs: n=113 NEW, 56% WR, +0.28%/trade, sum +$32
            #     (largest retro fire count of any trigger tested — fills the
            #      throughput gap left by the more-selective 4combo/quiet_pop).
            _trigger_deepbreakout_match = False
            _trigger_deepbreakout_reasons: list = []
            try:
                _db_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_db_cs) >= 11 and _db_cs[-1].open > 0:
                    _db_cur = _db_cs[-1]
                    if _db_cur.close > _db_cur.open:  # green close
                        _db_prior10_high = max(b.high for b in _db_cs[-11:-1])
                        _db_breakout = _db_cur.close > _db_prior10_high
                        _db_prior5_vols = [b.volume for b in _db_cs[-6:-1]]
                        _db_avg5 = (sum(_db_prior5_vols) / len(_db_prior5_vols)
                                    if _db_prior5_vols else 0)
                        _db_vol_ok = (_db_avg5 > 0
                                      and _db_cur.volume / _db_avg5 > 1.5)
                        if _db_breakout and _db_vol_ok:
                            _trigger_deepbreakout_match = True
                            _trigger_deepbreakout_reasons.append(
                                f"close>{_db_prior10_high:.6f} (10-bar high), "
                                f"vol={_db_cur.volume / _db_avg5:.2f}x avg5, green"
                            )
            except Exception as _e:
                logger.debug(f"[DipScanner] deep_breakout calc err: {_e}")

            # ── capitulation_v parallel trigger — ENFORCED 2026-05-06 ───────────
            # Fires when ALL THREE:
            #   1. macro15 < -15% (deep dump in 15 min)
            #   2. m15 > m30 + 5 (V-recovery already underway)
            #   3. vol > 1.5x avg of last 5 bars (real buying confirms reversal)
            #
            # The first capitulation-catch trigger. Fundamentally different from
            # all 4 prior triggers (which are reversal/breakout in stable
            # conditions). Captures the V-bottom moment where a token has
            # dumped hard AND is starting to recover within the same 30-min
            # window.
            #
            # Validator (scripts/validate_trigger.py):
            #   - Sim trigger_only marginal: n=71, 66% WR, avg=+2.12%/trade
            #   - Retro on bot pairs: n=9 NEW, 78% WR, +4.45%/trade, sum +$40
            #     (CDsvrN5KXi caught 5x in a row — all wins, +12.8% x 3)
            #   - Highest avg/trade of any trigger validated.
            _trigger_capitv_match = False
            _trigger_capitv_reasons: list = []
            try:
                _cv_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if (len(_cv_cs) >= 31
                        and _cv_cs[-1].open > 0
                        and _cv_cs[-16].close > 0
                        and _cv_cs[-31].close > 0):
                    _cv_cur = _cv_cs[-1]
                    _cv_m15 = (_cv_cur.close / _cv_cs[-16].close - 1) * 100
                    _cv_m30 = (_cv_cur.close / _cv_cs[-31].close - 1) * 100
                    _cv_dump = _cv_m15 < -15
                    _cv_recovery = _cv_m15 > _cv_m30 + 5
                    _cv_prior5_vols = [b.volume for b in _cv_cs[-6:-1]]
                    _cv_avg5 = (sum(_cv_prior5_vols) / len(_cv_prior5_vols)
                                if _cv_prior5_vols else 0)
                    _cv_vol_ok = (_cv_avg5 > 0
                                  and _cv_cur.volume / _cv_avg5 > 1.5)
                    if _cv_dump and _cv_recovery and _cv_vol_ok:
                        _trigger_capitv_match = True
                        _trigger_capitv_reasons.append(
                            f"m15={_cv_m15:+.1f}% (deep dump), "
                            f"m15-m30={_cv_m15 - _cv_m30:+.1f} (V-recovery), "
                            f"vol={_cv_cur.volume / _cv_avg5:.2f}x avg5"
                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] capitv calc err: {_e}")

            # ── engulf_at_low parallel trigger — ENFORCED 2026-05-06 ───────────
            # Fires when ALL THREE:
            #   1. Bullish engulfing — prev RED, current GREEN, current open
            #      <= prev close AND current close >= prev open (current
            #      candle's body fully engulfs prev candle's body)
            #   2. Prev bar's low <= 10-bar low * 1.005 (engulfing at swing low)
            #   3. Current close > max high of last 10 bars (breakout above
            #      prior 10-bar structure)
            #
            # Triple-confirmation reversal: sweep-the-low, engulfing reverse,
            # break out above prior structure. Most selective shape of any
            # shipped trigger.
            #
            # Different from capit_v (deep dump + V-recovery) and
            # deep_breakout (just 10-bar high + vol): this requires the
            # specific 1-bar engulfing reversal AT the swing low.
            #
            # Validator (scripts/validate_trigger.py):
            #   - Sim trigger_only: n=324 (4.5x capit_v), 67.3% WR, +0.90%/trade,
            #     sum +$293 ✓ — highest-volume sim of any trigger validated
            #   - Retro on bot pairs: n=12, 75% WR, +1.11%/trade, sum +$13 ✓
            #     2 clean +12.8% wins (CDsvrN5KXi, 3KHMZhpthX), 1 -12% stop,
            #     8 flats (mostly PAYmo6moDF dead-token noise)
            _trigger_engulflow_match = False
            _trigger_engulflow_reasons: list = []
            try:
                _el_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if (len(_el_cs) >= 11
                        and _el_cs[-1].open > 0
                        and _el_cs[-2].open > 0):
                    _el_cur = _el_cs[-1]
                    _el_prev = _el_cs[-2]
                    # 1. Bullish engulfing
                    _el_engulf = (
                        _el_prev.close < _el_prev.open  # prev red
                        and _el_cur.close > _el_cur.open  # current green
                        and _el_cur.open <= _el_prev.close
                        and _el_cur.close >= _el_prev.open
                    )
                    if _el_engulf:
                        _el_last10 = _el_cs[-11:-1]
                        _el_low10 = min(b.low for b in _el_last10)
                        _el_high10 = max(b.high for b in _el_last10)
                        # 2. Prev low at 10-bar swing low
                        _el_at_low = (_el_low10 > 0
                                      and _el_prev.low <= _el_low10 * 1.005)
                        # 3. Current close breaks 10-bar high
                        _el_breakout = _el_cur.close > _el_high10
                        if _el_at_low and _el_breakout:
                            _trigger_engulflow_match = True
                            _trigger_engulflow_reasons.append(
                                f"engulf at 10bar-low (prev_l={_el_prev.low:.6f} "
                                f"≤ {_el_low10*1.005:.6f}), close>{_el_high10:.6f}"
                            )
            except Exception as _e:
                logger.debug(f"[DipScanner] engulflow calc err: {_e}")

            # ── hc4_6pct parallel trigger — ENFORCED 2026-05-07 ────────────────
            # Fires when ALL TWO:
            #   1. cons_HC_4 — 4 consecutive higher CLOSES in bars[-5..-1]
            #      (each bar's close > prior bar's close, strictly monotonic)
            #   2. Current bar GREEN with body > 6% of open
            #
            # The discovery breakthrough of 2026-05-06: HL_k OR HC_k regime
            # detector (k≥4 strict monotonic) + power-green body is a robust
            # trigger class. The 6% body threshold is the precision sweet spot
            # — body Pareto is monotonic up to 6%, with diminishing returns
            # beyond.
            #
            # Validator (scripts/validate_trigger.py) of ~30 candidates in the
            # class — hc4_6pct was the dollar-sum champion:
            #   - Sim trigger_only: n=160, 57.7% WR, +0.69%/trade, +$110
            #   - Retro on bot pairs: n=7 NEW, 71.4% WR, +6.37%/trade, +$45
            #     CDsvrN5KXi: 3 wins (+12.80, +12.80, +12.80), 1 loss
            #     35Lod8esDj: 1 win (+12.80)
            #
            # Mechanism: HC_4 (4 sequential higher closes) is a regime detector
            # that structurally cannot fire during cascading declines (which
            # have falling closes). The 6% body filter requires a power-thrust
            # candle, screening out weak bounces that produce -12% stops.
            _trigger_hc46_match = False
            _trigger_hc46_reasons: list = []
            try:
                _hc_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_hc_cs) >= 5 and _hc_cs[-1].open > 0:
                    _hc_cur = _hc_cs[-1]
                    if _hc_cur.close > _hc_cur.open:  # green
                        _hc_body_pct = ((_hc_cur.close - _hc_cur.open)
                                        / _hc_cur.open * 100)
                        if _hc_body_pct > 6:
                            # cons_HC_4: closes monotonically rising over last 5 bars
                            _hc_closes = [_hc_cs[-k].close for k in (5, 4, 3, 2, 1)]
                            if (_hc_closes[0] < _hc_closes[1]
                                    < _hc_closes[2] < _hc_closes[3]
                                    < _hc_closes[4]):
                                _trigger_hc46_match = True
                                _trigger_hc46_reasons.append(
                                    f"HC_4 (4 higher closes), "
                                    f"body={_hc_body_pct:.2f}%"
                                )
            except Exception as _e:
                logger.debug(f"[DipScanner] hc46 calc err: {_e}")

            # ── squeeze_pullback parallel trigger — SHADOW 2026-05-06 ──────────
            # SHADOW MODE: computed and logged but NOT in _triggers_fired.
            # Gathering retro data before enforcement.
            #
            # Fires when ALL FOUR:
            #   1. Last 3 bars all had vol < 0.5x avg of bars[-13..-3]
            #      (TIGHT squeeze, tighter than quiet_pop's 0.8x)
            #   2. Current bar GREEN close
            #   3. macro15 in [-10%, -2%] (moderate pullback)
            #   4. 3 consecutive higher_lows (bars[-4..-1])
            #
            # Validator (scripts/validate_trigger.py):
            #   - Sim trigger_only: n=108, 69% WR, +2.31%/trade, +$249 ✓
            #   - Retro NEW: n=3 (too small to evaluate)
            #     All 3 fires on PAYmo6moDF — yellow flag that 0.5x squeeze
            #     may pick up DEAD tokens (no volume from disinterest) rather
            #     than COILING tokens (accumulation in tight range).
            #
            # Shadow ship lets us collect retro data over the next days/weeks
            # to confirm or refute the sim signal before enforcement.
            _trigger_squeeze_match = False
            _trigger_squeeze_reasons: list = []
            try:
                _sq_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_sq_cs) >= 16 and _sq_cs[-1].open > 0:
                    _sq_cur = _sq_cs[-1]
                    if _sq_cur.close > _sq_cur.open:  # green
                        _sq_avg10 = sum(b.volume for b in _sq_cs[-13:-3]) / 10
                        if _sq_avg10 > 0:
                            _sq_squeeze = all(
                                b.volume < _sq_avg10 * 0.5
                                for b in _sq_cs[-4:-1]
                            )
                            _sq_bar15 = _sq_cs[-16]
                            if _sq_squeeze and _sq_bar15.close > 0:
                                _sq_m15 = (_sq_cur.close / _sq_bar15.close - 1) * 100
                                if -10 <= _sq_m15 <= -2:
                                    _sq_lows = [_sq_cs[-k].low for k in (4, 3, 2, 1)]
                                    if (_sq_lows[0] < _sq_lows[1]
                                            < _sq_lows[2] < _sq_lows[3]):
                                        _trigger_squeeze_match = True
                                        _trigger_squeeze_reasons.append(
                                            f"3-tight (last3<{_sq_avg10*0.5:.0f}), "
                                            f"m15={_sq_m15:+.1f}% pull, 3HL"
                                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] squeeze calc err: {_e}")

            if _trigger_squeeze_match:
                logger.info(
                    f"[DipScanner] SHADOW trigger_squeeze_pullback fired: "
                    f"{token_symbol} {','.join(_trigger_squeeze_reasons)}"
                )

            # ── coil_long parallel trigger — ENFORCED 2026-05-07 ───────────────
            # 8th parallel trigger. Mechanically orthogonal to HC_k/clean_break:
            # fires after a sustained low-volatility coil rather than after
            # bearish reds (clean_break) or monotonic up-streak (HC_4).
            #
            # Conditions:
            #   - Last 7 bars all had range_pct < 2.0% (tight 7-bar coil)
            #   - Current bar is GREEN
            #   - Current body > 4.0%
            # I.e., 7 bars of accumulation/quiet → 4%+ green expansion.
            #
            # Validator (scripts/validate_trigger.py) on 249 token-batches:
            #   - Sim trigger_only: n=269, 60.6% WR, +0.75%/trade, +$203
            #     67% of fires happen on bars where clean_break doesn't —
            #     genuinely orthogonal to existing triggers.
            #   - Retro on bot pairs: n=11 NEW, 75% WR, +2.52%/trade, +$28
            #     Wins clustered on 35Lod (3 wins) + J8PSdNP3Qe (2 wins).
            #
            # Pareto search of close variants (range<1.5%, 5-bar coil, 5%-body
            # variants) showed this 7-bar/<2%/4% setup as best. Tighter range
            # (1.5%) failed retro; larger body (5%) failed both tiers.
            _trigger_coillong_match = False
            _trigger_coillong_reasons: list = []
            try:
                _cl_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_cl_cs) >= 8 and _cl_cs[-1].open > 0:
                    _cl_cur = _cl_cs[-1]
                    if _cl_cur.close > _cl_cur.open:  # green
                        _cl_body_pct = ((_cl_cur.close - _cl_cur.open)
                                        / _cl_cur.open * 100)
                        if _cl_body_pct > 4:
                            _cl_coil_ok = True
                            for _cl_k in (8, 7, 6, 5, 4, 3, 2):
                                _cl_b = _cl_cs[-_cl_k]
                                if _cl_b.open <= 0:
                                    _cl_coil_ok = False
                                    break
                                _cl_rng = (_cl_b.high - _cl_b.low) / _cl_b.open * 100
                                if _cl_rng >= 2.0:
                                    _cl_coil_ok = False
                                    break
                            if _cl_coil_ok:
                                _trigger_coillong_match = True
                                _trigger_coillong_reasons.append(
                                    f"7-bar coil (all rng<2%), "
                                    f"body={_cl_body_pct:.2f}%"
                                )
            except Exception as _e:
                logger.debug(f"[DipScanner] coillong calc err: {_e}")

            # ── range_decay_4bar parallel trigger — ENFORCED 2026-05-07 ────────
            # 9th parallel trigger. Volatility compression climax: last 4 bars
            # all had ranges strictly monotonically declining + green expansion
            # > 4%. Different from coil_long (uniformly tight ranges) — this
            # requires ACTIVELY narrowing intrabar volatility, indicating
            # increasing equilibrium pressure before release.
            #
            # Validator on 249 token-batches:
            #   - Sim trigger_only: n=72, 65.7% WR, +1.75%/trade, +$126
            #   - Retro NEW: n=7, 57.1% WR, +0.64%/trade, +$5
            # Pareto search confirmed 4-bar floor: 3-bar variant retro fails
            # (-4.05%/trade); 5-bar variant n=20 too small to evaluate.
            _trigger_decay4_match = False
            _trigger_decay4_reasons: list = []
            try:
                _d4_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_d4_cs) >= 5 and _d4_cs[-1].open > 0:
                    _d4_cur = _d4_cs[-1]
                    if _d4_cur.close > _d4_cur.open:
                        _d4_body = ((_d4_cur.close - _d4_cur.open)
                                    / _d4_cur.open * 100)
                        if _d4_body > 4:
                            _d4_ranges = []
                            _d4_ok = True
                            for _d4_k in (5, 4, 3, 2):
                                _d4_b = _d4_cs[-_d4_k]
                                if _d4_b.open <= 0:
                                    _d4_ok = False
                                    break
                                _d4_ranges.append(_d4_b.high - _d4_b.low)
                            if _d4_ok and len(_d4_ranges) == 4:
                                if (_d4_ranges[0] > _d4_ranges[1]
                                        > _d4_ranges[2] > _d4_ranges[3]):
                                    _trigger_decay4_match = True
                                    _trigger_decay4_reasons.append(
                                        f"4-bar range strict decline, "
                                        f"body={_d4_body:.2f}%"
                                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] decay4 calc err: {_e}")

            # ── range_decay_4of5 parallel trigger — ENFORCED 2026-05-07 ────────
            # 10th parallel trigger. Looser than range_decay_4bar — allows 1
            # outlier in 5 range comparisons across last 6 bars. Higher
            # throughput, slightly lower WR. Catches compression patterns where
            # one bar punctuates the otherwise-narrowing trend.
            #
            # Validator on 249 token-batches:
            #   - Sim trigger_only: n=133, 61.9% WR, +0.89%/trade, +$119
            #   - Retro NEW: n=15, 53.3% WR, +0.58%/trade, +$9
            _trigger_decay4of5_match = False
            _trigger_decay4of5_reasons: list = []
            try:
                _d5_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_d5_cs) >= 7 and _d5_cs[-1].open > 0:
                    _d5_cur = _d5_cs[-1]
                    if _d5_cur.close > _d5_cur.open:
                        _d5_body = ((_d5_cur.close - _d5_cur.open)
                                    / _d5_cur.open * 100)
                        if _d5_body > 4:
                            _d5_ranges = []
                            _d5_ok = True
                            for _d5_k in (7, 6, 5, 4, 3, 2):
                                _d5_b = _d5_cs[-_d5_k]
                                if _d5_b.open <= 0:
                                    _d5_ok = False
                                    break
                                _d5_ranges.append(_d5_b.high - _d5_b.low)
                            if _d5_ok and len(_d5_ranges) == 6:
                                _d5_declines = sum(
                                    1 for _d5_i in range(5)
                                    if _d5_ranges[_d5_i] > _d5_ranges[_d5_i + 1]
                                )
                                if (_d5_declines >= 4
                                        and _d5_ranges[0] > _d5_ranges[-1]):
                                    _trigger_decay4of5_match = True
                                    _trigger_decay4of5_reasons.append(
                                        f"4-of-5 range declines, "
                                        f"body={_d5_body:.2f}%"
                                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] decay4of5 calc err: {_e}")

            # ── coil_top_vol parallel trigger — ENFORCED 2026-05-07 ────────────
            # 11th parallel trigger. Compound of coil_long (7-bar uniform tight
            # range) + top-decile volume (cur bar vol > max of prior 30). The
            # vol gate filters coil_long's noisy false-breakouts to real
            # volume-confirmed releases.
            #
            # Validator on 295 token-batches:
            #   - Sim trigger_only: n=174, 63.4% WR, +1.06%/trade, +$185
            #   - Retro NEW: n=5, 66.7% WR, +0.86%/trade, +$4
            # Improves on base coil_long (60.6% WR) by 2.8pp WR + 41% avg.
            _trigger_coiltv_match = False
            _trigger_coiltv_reasons: list = []
            try:
                _ctv_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_ctv_cs) >= 31 and _ctv_cs[-1].open > 0:
                    _ctv_cur = _ctv_cs[-1]
                    if _ctv_cur.close > _ctv_cur.open:
                        _ctv_body_pct = ((_ctv_cur.close - _ctv_cur.open)
                                         / _ctv_cur.open * 100)
                        if _ctv_body_pct > 4:
                            _ctv_coil_ok = True
                            for _ctv_k in (8, 7, 6, 5, 4, 3, 2):
                                _ctv_b = _ctv_cs[-_ctv_k]
                                if _ctv_b.open <= 0:
                                    _ctv_coil_ok = False
                                    break
                                _ctv_rng = (_ctv_b.high - _ctv_b.low) / _ctv_b.open * 100
                                if _ctv_rng >= 2.0:
                                    _ctv_coil_ok = False
                                    break
                            if _ctv_coil_ok:
                                _ctv_cur_vol = _ctv_cur.volume or 0
                                if _ctv_cur_vol > 0:
                                    _ctv_prior_max = max(
                                        (_ctv_cs[-_ctv_k].volume or 0)
                                        for _ctv_k in range(2, 32)
                                    )
                                    if _ctv_cur_vol > _ctv_prior_max:
                                        _trigger_coiltv_match = True
                                        _trigger_coiltv_reasons.append(
                                            f"7-bar coil + top-30 vol, "
                                            f"body={_ctv_body_pct:.2f}%"
                                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] coil_top_vol calc err: {_e}")

            # ── decay_5bar parallel trigger — SHADOW 2026-05-07 ────────────────
            # SHADOW MODE: computed and logged but NOT in _triggers_fired.
            # Gathering forward data on the rare-but-stellar 5-bar strict decay
            # pattern.
            #
            # Validator on 295 token-batches:
            #   - Sim trigger_only: n=26, 87.5% WR, +5.42%/trade, +$141
            #   - Retro NEW: n=2, 2W/0L, +10.03%/trade
            # Sample below the n=200 evaluable threshold but signal has held
            # as dataset grew (n=20 -> n=26 kept WR ~88%). Forward retro
            # collection will determine if the signal generalizes or is
            # historical noise.
            _trigger_decay5_match = False
            _trigger_decay5_reasons: list = []
            try:
                _d5b_cs = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_d5b_cs) >= 6 and _d5b_cs[-1].open > 0:
                    _d5b_cur = _d5b_cs[-1]
                    if _d5b_cur.close > _d5b_cur.open:
                        _d5b_body = ((_d5b_cur.close - _d5b_cur.open)
                                     / _d5b_cur.open * 100)
                        if _d5b_body > 4:
                            _d5b_ranges = []
                            _d5b_ok = True
                            for _d5b_k in (6, 5, 4, 3, 2):
                                _d5b_b = _d5b_cs[-_d5b_k]
                                if _d5b_b.open <= 0:
                                    _d5b_ok = False
                                    break
                                _d5b_ranges.append(_d5b_b.high - _d5b_b.low)
                            if _d5b_ok and len(_d5b_ranges) == 5:
                                if (_d5b_ranges[0] > _d5b_ranges[1]
                                        > _d5b_ranges[2] > _d5b_ranges[3]
                                        > _d5b_ranges[4]):
                                    _trigger_decay5_match = True
                                    _trigger_decay5_reasons.append(
                                        f"5-bar strict decline, "
                                        f"body={_d5b_body:.2f}%"
                                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] decay5 calc err: {_e}")

            if _trigger_decay5_match:
                logger.info(
                    f"[DipScanner] SHADOW trigger_decay_5bar fired: "
                    f"{token_symbol} {','.join(_trigger_decay5_reasons)}"
                )

            # ── momentum_continuation parallel trigger — ENFORCED 2026-05-07 PM
            # Fires when:
            #   1. Current 1m candle is green
            #   2. 4 consec green 1m bars (current + 3 prior all green)
            #   3. Current vol > 1.5x avg of last 30 1m bars
            #
            # Catches MOMENTUM CONTINUATION — fundamentally different from
            # clean_break (dip pullback first-green-after-reds). When 4
            # consecutive bars close green AND volume is rising relative
            # to recent average, the trend is real and often runs another
            # 8%+ in 20 minutes.
            #
            # Validation (validate_trigger.py 2026-05-07):
            #   SIM:   trigger_only n=1092, WR=65.7%, avg=+1.75%/trade
            #          (vs cb_only baseline 52.9% WR, -0.35%/trade)
            #          ZERO overlap with clean_break — fully orthogonal.
            #   RETRO: n=54 NEW marginal entries on bot-traded pairs,
            #          WR=55.2%, avg=+0.04%/trade. Modest but positive.
            #
            # Mined from FAST_WIN vs LOSER discrimination on master bar
            # dataset (n=295 tokens). Best 2-feature combo for fast +8%
            # moves within 20 min: 4+ consec green AND vol_spike_30 >= 1.5.
            _trigger_momentum_continuation_match = False
            _trigger_momentum_continuation_reasons: list = []
            try:
                _mc_cs = (_chart_data.candles_1m
                          if _chart_data and _chart_data.candles_1m else [])
                if len(_mc_cs) >= 31:
                    _mc_cur = _mc_cs[-1]
                    if (_mc_cur.open > 0
                            and _mc_cur.close > _mc_cur.open):
                        # 4 consec green check
                        _mc_4_green = True
                        for k in (1, 2, 3, 4):
                            _mc_b = _mc_cs[-k]
                            if (_mc_b.open <= 0
                                    or _mc_b.close <= _mc_b.open):
                                _mc_4_green = False
                                break
                        if _mc_4_green:
                            _mc_prior30 = _mc_cs[-31:-1]
                            _mc_vols = [b.volume for b in _mc_prior30
                                        if b.volume is not None]
                            if len(_mc_vols) >= 20:
                                _mc_avg30 = sum(_mc_vols) / len(_mc_vols)
                                if _mc_avg30 > 0:
                                    _mc_vol_ratio = _mc_cur.volume / _mc_avg30
                                    if _mc_vol_ratio >= 1.5:
                                        _trigger_momentum_continuation_match = True
                                        _trigger_momentum_continuation_reasons.append(
                                            f"4 consec green 1m, "
                                            f"vol={_mc_vol_ratio:.2f}x avg30 "
                                            f"(momentum continuation)"
                                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] momentum_continuation calc err: {_e}")

            # ── explosive_break parallel trigger — ENFORCED 2026-05-07 PM ──────
            # Fires when ALL FOUR:
            #   1. Current 1m green
            #   2. 1m range >= 4x avg of last 5 bars (explosive expansion)
            #   3. 5m vol >= 4x avg of prior 5 5m bars (volume confirmation)
            #   4. 3+ consec green 5m bars (5m TF momentum confirmed)
            #   5. 1m cum_3min_pct >= +1% (already recovering on 1m)
            #
            # Tightened version of the threshold sweep — highest WR (66.7%)
            # of any tested variant on the fast-mover cohort.
            #
            # Validation (fast-mover retro, n=251 tokens with >=10% in 20min):
            #   n=99 fires, WR=66.7%, avg=+1.53%, TP1=39.4%, Stop=16.2%
            #   Lowest stop rate of any tested variant (high conviction).
            #
            # Catches explosive multi-timeframe breakouts where 1m, 5m, and
            # vol all confirm simultaneously. Different mechanism from
            # momentum_continuation (which is 1m-only).
            _trigger_explosive_break_match = False
            _trigger_explosive_break_reasons: list = []
            try:
                _eb_cs1 = (_chart_data.candles_1m
                           if _chart_data and _chart_data.candles_1m else [])
                _eb_cs5 = (_chart_data.candles_5m
                           if _chart_data and _chart_data.candles_5m else [])
                if (len(_eb_cs1) >= 6 and len(_eb_cs5) >= 6):
                    _eb_cur1 = _eb_cs1[-1]
                    if _eb_cur1.open > 0 and _eb_cur1.close > _eb_cur1.open:
                        # 1m range expansion: >= 4x avg of last 5 bars
                        _eb_cur_range = (_eb_cur1.high - _eb_cur1.low) / _eb_cur1.open * 100
                        _eb_last5_ranges = [
                            (b.high - b.low) / b.open * 100
                            for b in _eb_cs1[-6:-1] if b.open > 0
                        ]
                        if len(_eb_last5_ranges) == 5:
                            _eb_avg5_range = sum(_eb_last5_ranges) / 5
                            if _eb_avg5_range > 0:
                                _eb_range_ratio = _eb_cur_range / _eb_avg5_range
                                # 1m cum_3min_pct
                                _eb_cum3 = m1_features.get("1m_cum_3min_pct")
                                # 5m TF
                                _eb_cur5 = _eb_cs5[-1]
                                _eb_consec_g5 = 0
                                for b in reversed(_eb_cs5[-5:]):
                                    if b.close > b.open:
                                        _eb_consec_g5 += 1
                                    else:
                                        break
                                # 5m vol spike vs prior 5
                                _eb_prior5m_vols = [
                                    b.volume for b in _eb_cs5[-6:-1]
                                    if b.volume is not None
                                ]
                                if (_eb_range_ratio >= 4.0
                                        and _eb_cur5.open > 0
                                        and _eb_cur5.close > _eb_cur5.open
                                        and _eb_consec_g5 >= 3
                                        and len(_eb_prior5m_vols) == 5):
                                    _eb_avg5m_vol = sum(_eb_prior5m_vols) / 5
                                    if _eb_avg5m_vol > 0:
                                        _eb_5m_vol_ratio = (_eb_cur5.volume
                                                             / _eb_avg5m_vol)
                                        if (_eb_5m_vol_ratio >= 4.0
                                                and _eb_cum3 is not None
                                                and float(_eb_cum3) >= 1.0):
                                            _trigger_explosive_break_match = True
                                            _trigger_explosive_break_reasons.append(
                                                f"1m_range={_eb_range_ratio:.1f}x avg5, "
                                                f"5m_vol={_eb_5m_vol_ratio:.1f}x avg5, "
                                                f"5m_consec_green={_eb_consec_g5}, "
                                                f"1m_cum3={float(_eb_cum3):+.2f}%"
                                            )
            except Exception as _e:
                logger.debug(f"[DipScanner] explosive_break calc err: {_e}")

            # ── range_expansion_qualified parallel trigger — ENFORCED 2026-05-07 PM
            # Fires when ALL FOUR:
            #   1. Current 1m green
            #   2. 1m range >= 6x avg of last 5 bars (deeply explosive)
            #   3. Current vol >= 2x avg of last 30 bars (vol confirmation)
            #   4. 1m cum_3min_pct >= +1% (active recovery)
            #   5. 3+ higher-highs in last 5 bars (price-action momentum)
            #
            # Validation (fast-mover retro):
            #   n=568 fires, WR=60.4%, avg=+1.27%, TP1=49.3%, Stop=17.3%
            #   Higher fire count than explosive_break, slightly lower WR.
            #
            # Wider 1m signal — doesn't require 5m TF alignment, just
            # very strong 1m candle expansion. Complements explosive_break.
            _trigger_range_expansion_qualified_match = False
            _trigger_range_expansion_qualified_reasons: list = []
            try:
                _re_cs = (_chart_data.candles_1m
                          if _chart_data and _chart_data.candles_1m else [])
                if len(_re_cs) >= 31:
                    _re_cur = _re_cs[-1]
                    if _re_cur.open > 0 and _re_cur.close > _re_cur.open:
                        _re_cur_range = (_re_cur.high - _re_cur.low) / _re_cur.open * 100
                        _re_last5_ranges = [
                            (b.high - b.low) / b.open * 100
                            for b in _re_cs[-6:-1] if b.open > 0
                        ]
                        if len(_re_last5_ranges) == 5:
                            _re_avg5_range = sum(_re_last5_ranges) / 5
                            if _re_avg5_range > 0:
                                _re_range_ratio = _re_cur_range / _re_avg5_range
                                # vol vs 30-bar avg
                                _re_prior30_vols = [
                                    b.volume for b in _re_cs[-31:-1]
                                    if b.volume is not None
                                ]
                                _re_cum3 = m1_features.get("1m_cum_3min_pct")
                                # higher-highs
                                _re_last5_bars = _re_cs[-5:]
                                _re_hh = sum(
                                    1 for j in range(1, 5)
                                    if _re_last5_bars[j].high > _re_last5_bars[j-1].high
                                )
                                if (_re_range_ratio >= 6.0
                                        and len(_re_prior30_vols) >= 25):
                                    _re_avg30_vol = (sum(_re_prior30_vols)
                                                      / len(_re_prior30_vols))
                                    if _re_avg30_vol > 0:
                                        _re_vol_ratio = _re_cur.volume / _re_avg30_vol
                                        if (_re_vol_ratio >= 2.0
                                                and _re_cum3 is not None
                                                and float(_re_cum3) >= 1.0
                                                and _re_hh >= 3):
                                            _trigger_range_expansion_qualified_match = True
                                            _trigger_range_expansion_qualified_reasons.append(
                                                f"range={_re_range_ratio:.1f}x avg5, "
                                                f"vol={_re_vol_ratio:.1f}x avg30, "
                                                f"cum3={float(_re_cum3):+.2f}%, "
                                                f"hh={_re_hh}"
                                            )
            except Exception as _e:
                logger.debug(f"[DipScanner] range_expansion_qualified err: {_e}")

            # ── 6of7_green_vol parallel trigger — ENFORCED 2026-05-07 PM ───────
            # Fires when:
            #   1. Current 1m green
            #   2. 6 of last 7 1m bars are green (any positions; 1 red OK)
            #   3. Current vol >= 1.5x avg of last 30 1m bars
            #
            # Catches "mostly green sequences" — different from
            # momentum_continuation (strict 4 consec). Many sustained climbs
            # have one red wobble bar broken before resuming.
            #
            # Validation (fast-mover retro, n=251 tokens):
            #   n=1708, WR=63.7%, avg=+1.36%, TP=57.5%, Stop=22.9%
            _trigger_6of7_green_vol_match = False
            _trigger_6of7_green_vol_reasons: list = []
            try:
                _g7_cs = (_chart_data.candles_1m
                          if _chart_data and _chart_data.candles_1m else [])
                if len(_g7_cs) >= 31:
                    _g7_cur = _g7_cs[-1]
                    if _g7_cur.open > 0 and _g7_cur.close > _g7_cur.open:
                        _g7_last7 = _g7_cs[-7:]
                        _g7_greens = sum(
                            1 for b in _g7_last7
                            if b.open > 0 and b.close > b.open
                        )
                        if _g7_greens >= 6:
                            _g7_prior30 = _g7_cs[-31:-1]
                            _g7_vols = [b.volume for b in _g7_prior30
                                        if b.volume is not None]
                            if len(_g7_vols) >= 20:
                                _g7_avg30 = sum(_g7_vols) / len(_g7_vols)
                                if _g7_avg30 > 0:
                                    _g7_ratio = _g7_cur.volume / _g7_avg30
                                    if _g7_ratio >= 1.5:
                                        _trigger_6of7_green_vol_match = True
                                        _trigger_6of7_green_vol_reasons.append(
                                            f"{_g7_greens}/7 green, "
                                            f"vol={_g7_ratio:.2f}x avg30 "
                                            f"(mostly-green sequence)"
                                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] 6of7_green_vol calc err: {_e}")

            # ── hh10_strict_vol parallel trigger — ENFORCED 2026-05-07 PM ──────
            # Fires when:
            #   1. Current 1m green
            #   2. 7+ higher-highs in last 10 1m bars
            #   3. Current vol >= 1.5x avg of last 30 1m bars
            #
            # Different mechanism from consec-green triggers — uses HH count
            # for price-action strength. Catches climbs where greens aren't
            # strictly consecutive but highs keep stepping up.
            #
            # Validation (fast-mover retro):
            #   n=2304, WR=61.3%, avg=+1.03%, TP=53.0%, Stop=22.4%
            _trigger_hh10_strict_vol_match = False
            _trigger_hh10_strict_vol_reasons: list = []
            try:
                _hh_cs = (_chart_data.candles_1m
                          if _chart_data and _chart_data.candles_1m else [])
                if len(_hh_cs) >= 31:
                    _hh_cur = _hh_cs[-1]
                    if _hh_cur.open > 0 and _hh_cur.close > _hh_cur.open:
                        _hh_last10 = _hh_cs[-10:]
                        _hh_count = sum(
                            1 for j in range(1, 10)
                            if _hh_last10[j].high > _hh_last10[j-1].high
                        )
                        if _hh_count >= 7:
                            _hh_prior30 = _hh_cs[-31:-1]
                            _hh_vols = [b.volume for b in _hh_prior30
                                        if b.volume is not None]
                            if len(_hh_vols) >= 20:
                                _hh_avg30 = sum(_hh_vols) / len(_hh_vols)
                                if _hh_avg30 > 0:
                                    _hh_ratio = _hh_cur.volume / _hh_avg30
                                    if _hh_ratio >= 1.5:
                                        _trigger_hh10_strict_vol_match = True
                                        _trigger_hh10_strict_vol_reasons.append(
                                            f"{_hh_count}/9 HH in last 10, "
                                            f"vol={_hh_ratio:.2f}x avg30 "
                                            f"(stepping-up trend)"
                                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] hh10_strict_vol calc err: {_e}")

            # ── hh10_8plus parallel trigger — ENFORCED 2026-05-07 PM ───────────
            # Fires when:
            #   1. Current 1m green
            #   2. 8+ higher-highs in last 10 1m bars (no vol gate)
            #
            # Pure price-action strength. Distinct from hh10_strict_vol which
            # requires HH>=7 AND vol>=1.5x — this catches tokens making
            # stepwise climbs without vol explosion.
            #
            # Validation across 3 fast-mover cohort definitions:
            #   +15%/60min: WR=61.0%, +$1.10/trade, n=2135, Stop=20.7%
            #   +20%/90min: WR=62.0%, +$1.21/trade, n=2090, Stop=20.8%
            #   +12%/30min: WR=60.6%, +$1.03/trade, n=2180, Stop=20.6%
            # Lowest stop rate of any candidate tested.
            _trigger_hh10_8plus_match = False
            _trigger_hh10_8plus_reasons: list = []
            try:
                _h8_cs = (_chart_data.candles_1m
                          if _chart_data and _chart_data.candles_1m else [])
                if len(_h8_cs) >= 10:
                    _h8_cur = _h8_cs[-1]
                    if _h8_cur.open > 0 and _h8_cur.close > _h8_cur.open:
                        _h8_last10 = _h8_cs[-10:]
                        _h8_count = sum(
                            1 for j in range(1, 10)
                            if _h8_last10[j].high > _h8_last10[j-1].high
                        )
                        if _h8_count >= 8:
                            _trigger_hh10_8plus_match = True
                            _trigger_hh10_8plus_reasons.append(
                                f"{_h8_count}/9 HH in last 10 (pure trend)"
                            )
            except Exception as _e:
                logger.debug(f"[DipScanner] hh10_8plus calc err: {_e}")

            # ── vol_velocity_2grn parallel trigger — ENFORCED 2026-05-07 PM ────
            # Fires when:
            #   1. Last 2 bars both green (1m_cur and 1m_prev)
            #   2. Volume strictly increasing over last 3 bars
            #      (v[-1] > v[-2] > v[-3]) — accelerating, not just spike
            #   3. Current body_pct >= 2.0%
            #   4. cur vol / avg(prior 30) >= 1.0x
            #
            # Mined from gap analysis: targets FAST_WIN bars that the prior
            # 18 triggers miss entirely. Velocity is a sequential signal —
            # vol_spike alone misses tokens with steady ramp.
            #
            # Multi-cohort validation (gap-only, uncaptured by 7 reference
            # triggers):
            #   +10%/20min cohort: WR=64.1%, +$1.42/trade, n=690, Stop=25.9%
            #   +15%/60min cohort: WR=65%+, similar profile
            #   +20%/90min cohort: WR=65%+, similar profile
            # Highest WR of any 19th-trigger candidate from this round.
            _trigger_vol_velocity_2grn_match = False
            _trigger_vol_velocity_2grn_reasons: list = []
            try:
                _vv_cs = (_chart_data.candles_1m
                          if _chart_data and _chart_data.candles_1m else [])
                if len(_vv_cs) >= 31:
                    _vv_cur = _vv_cs[-1]
                    _vv_p1 = _vv_cs[-2]
                    _vv_p2 = _vv_cs[-3]
                    if (_vv_cur.open > 0 and _vv_cur.close > _vv_cur.open
                            and _vv_p1.open > 0 and _vv_p1.close > _vv_p1.open):
                        _vv_v1 = _vv_cur.volume or 0
                        _vv_v2 = _vv_p1.volume or 0
                        _vv_v3 = _vv_p2.volume or 0
                        if _vv_v1 > _vv_v2 > _vv_v3 > 0:
                            _vv_body_pct = ((_vv_cur.close - _vv_cur.open)
                                            / _vv_cur.open * 100)
                            if _vv_body_pct >= 2.0:
                                _vv_p30 = _vv_cs[-31:-1]
                                _vv_vols = [b.volume for b in _vv_p30
                                            if b.volume is not None]
                                if _vv_vols:
                                    _vv_avg = sum(_vv_vols) / len(_vv_vols)
                                    if _vv_avg > 0 and _vv_v1 / _vv_avg >= 1.0:
                                        _trigger_vol_velocity_2grn_match = True
                                        _trigger_vol_velocity_2grn_reasons.append(
                                            f"vol velocity {_vv_v3:.0f}->"
                                            f"{_vv_v2:.0f}->{_vv_v1:.0f}, "
                                            f"body={_vv_body_pct:+.2f}%>=2.0, "
                                            f"vol={_vv_v1/_vv_avg:.2f}x avg30 "
                                            f"(accelerating buyers, 2 grn)"
                                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] vol_velocity_2grn calc err: {_e}")

            # ── high_regime parallel trigger — ENFORCED 2026-05-07 PM ──────────
            # Additive trigger that fires on tokens passing all filters during
            # high-regime cycles with positive 1m momentum. Catches tokens
            # currently logged as "BLOCKED by all triggers" — i.e., tokens
            # that pass every entry-quality filter but don't match any candle
            # pattern.
            #
            # Fires when:
            #   1. regime_dip_breadth_pct >= 11 (broad market in pullback;
            #      bot's mean-reversion edge works at peak)
            #   2. 1m_cum_3min_pct >= 0 (1m already starting to recover)
            #
            # Validation (4 days, n=94 with regime feature):
            #   - Cohort hi_regime+pos_cum3: n=9, avg=$+1.26/trade, big-win 56%
            #   - Same cohort in low regime: n=20, avg=$-0.53 (FOME-trap)
            #   - $1.79/trade conditional swing — strongest single feature
            #     conditioning in the data
            #
            # Caveats: small sample (n=9 historical matches); fire rate is
            # narrow. Goal is to ADD entries on quality candidates we
            # currently miss, not to gate existing entries. Watch forward
            # data — demote to SHADOW if EV reverses.
            #
            # Hard-gate rewrite 2026-05-08 after analyzing 27 high_regime
            # fires from 2026-05-07: trigger was 41% WR / -$44.77 net,
            # dominated by GMAR x6 (dev=0.5%) and dead-volume entries.
            # The earlier conditional suppression gates (post_pump_dead_vol,
            # seller_dead_vol) were not strong enough.
            #
            # Replaced with two ABSOLUTE requirements that must both hold
            # for the trigger to fire:
            #   1. 1m_volume_spike >= 0.5  (real buying — losers had
            #      median vs=0.26, winners had vs=0.72; biggest single
            #      discriminator in the daily data)
            #   2. dev_pct_remaining >= 2.0  (creator hasn't dumped —
            #      kicks GMAR-style chronic dumpers where dev=0.5%)
            #
            # Both gates derive from analyze_high_regime_today.py:
            #   vs>=0.5 alone:               WR 64%, +$8.08, 11 fires
            #   dev>=2 alone:                WR 46%, -$17.58, 24 fires
            #   vs>=0.5 AND dev>=2 combined: WR 70%, +$15.74, 10 fires
            #
            # Net swing on today's data: -$44.77 -> +$15.74 (+$60).
            # Sample is small (10 fires kept) but signal is strong.
            _trigger_high_regime_match = False
            _trigger_high_regime_reasons: list = []
            try:
                _hr_cum3 = m1_features.get("1m_cum_3min_pct")
                _hr_vs = m1_features.get("1m_volume_spike")
                _hr_dev = _tier1_features.get("dev_pct_remaining")
                # mtf_vol_align gate added 2026-05-08 PM after 4 high_regime
                # losers in 22min (UAP, AMERICA, Clawd-3rd, UAP-rebuy), all
                # with mtfva=0. Validation: catches 3/3 today's losers,
                # preserves GAYTES (mtfva=2) and AALIEN (mtfva=1). Lifetime
                # pure-HR cohort swing -$51.74 -> +$0.11 (+$51.85), CV-stable.
                _cs1_hr = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                _cs5_hr = _chart_data.candles_5m if _chart_data and _chart_data.candles_5m else []
                _cs15_hr = _chart_data.candles_15m if _chart_data and _chart_data.candles_15m else []

                def _hr_vol_spike(series):
                    if not series or len(series) < 4:
                        return False
                    prior = [k.volume for k in series[-5:-1]]
                    if not prior:
                        return False
                    avg = sum(prior) / len(prior)
                    return avg > 0 and series[-1].volume / avg > 1.0

                _hr_mtfva = (int(_hr_vol_spike(_cs1_hr))
                             + int(_hr_vol_spike(_cs5_hr))
                             + int(_hr_vol_spike(_cs15_hr)))

                _hr_vs_ok = _hr_vs is not None and float(_hr_vs) >= 0.5
                _hr_dev_ok = _hr_dev is not None and float(_hr_dev) >= 2.0
                _hr_mtfva_ok = _hr_mtfva >= 1
                # Mechanism A: require filter_clean_break PASS (green-after-red
                # 1m candle confirmation). Added 2026-05-08 PM. Stops the
                # trigger from firing on the down-leg of a dip — waits for the
                # confirmed bounce candle. Today's UAP-1, UAP-2, Clawd-3rd all
                # had filter_clean_break BLOCK at entry (cr>=1 with negative
                # last_close); this gate would have skipped them.
                # Validated: lifetime high_regime cohort -$49 -> -$9 (+$40
                # swing). Catches today's 3 high_regime losers; doesn't catch
                # AMERICA-1 (had cr=0, lc=+0.88 — clean_break PASSes legit).
                _hr_clean_break_ok = (_filter_clean_break_verdict == "PASS")
                _hr_base_ok = (
                    _regime_dip_breadth_pct is not None
                    and _regime_dip_breadth_pct >= 11
                    and _hr_cum3 is not None
                    and float(_hr_cum3) >= 0
                )
                if (_hr_base_ok and _hr_vs_ok and _hr_dev_ok and _hr_mtfva_ok
                        and _hr_clean_break_ok):
                    _trigger_high_regime_match = True
                    _trigger_high_regime_reasons.append(
                        f"regime_dip_breadth={_regime_dip_breadth_pct:.1f}>=11 "
                        f"AND 1m_cum_3min={float(_hr_cum3):+.2f}>=0 "
                        f"AND vol_spike={float(_hr_vs):.2f}>=0.5 "
                        f"AND dev_pct={float(_hr_dev):.1f}%>=2.0 "
                        f"AND mtf_vol_align={_hr_mtfva}>=1 "
                        f"AND clean_break=PASS (green-after-red confirmed)"
                    )
                elif _hr_base_ok:
                    _why = []
                    if not _hr_vs_ok:
                        _vs_str = (f"{float(_hr_vs):.2f}"
                                   if _hr_vs is not None else "missing")
                        _why.append(f"vol_spike={_vs_str}<0.5")
                    if not _hr_dev_ok:
                        _dev_str = (f"{float(_hr_dev):.1f}%"
                                    if _hr_dev is not None else "missing")
                        _why.append(f"dev_pct={_dev_str}<2.0")
                    if not _hr_mtfva_ok:
                        _why.append(f"mtf_vol_align={_hr_mtfva}<1 (no tf vol spike)")
                    if not _hr_clean_break_ok:
                        _why.append("clean_break=BLOCK (no green confirmation candle yet)")
                    logger.info(
                        f"[DipScanner] high_regime SUPPRESSED: "
                        f"{token_symbol} {' AND '.join(_why)}"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] high_regime calc err: {_e}")

            # clean_break suppression gates — refined 2026-05-08 across
            # two analysis rounds on 26 clean_break fires from 2026-05-07.
            # All gates are validated as positive-swing on lifetime data.
            #
            #   Gate A: dev_pct_remaining < 1.0
            #     Catches GMAR x3 (dev=0.5%, all -12% stops, -$19.74).
            #     +$19.74 swing, zero winners killed.
            #   Gate B: peak_h24_6h >= 1500% AND 1m_volume_spike < 0.30
            #     Catches mask -$4.73 (peak=1676, vs=0.06).
            #     +$4.73 swing, zero winners killed.
            #   Gate C: chart_pattern_5m_conf >= 80
            #     Catches AMERICA -$4.26 (conf=100), oGNOME -$1.88 (conf=99.9),
            #     SELLOR +$0.57 (conf=115). Mechanism: in distribution
            #     contexts, "textbook" 5m bullish patterns are bull-traps.
            #     +$5.57 swing.
            #   Gate D: regime_dip_breadth_pct < 10
            #     Catches Hantavax -$1.46 (regime=9.8). Low coordination =
            #     isolated bounce in unrelated market is noise.
            #     +$1.46 swing, zero winners killed.
            #
            # Combined today's swing: clean_break -$2.03 -> +$29.47
            # ($31.50 lifetime improvement on 24 trades).
            _cb_gated = False
            _cb_gate_reason = ""
            if _filter_clean_break_verdict == "PASS":
                try:
                    _cb_dev = _tier1_features.get("dev_pct_remaining")
                    _cb_vs = m1_features.get("1m_volume_spike")
                    _cb_peak = float(peak_h24_6h or 0)
                    _cb_chart_conf = None
                    try:
                        _cb_chart_conf = _chart_ctx.pattern_5m.get("confidence")
                    except Exception:
                        pass
                    _cb_regime = _regime_dip_breadth_pct
                    # Compound-trigger carve-out: when BOTH clean_break PASS
                    # AND the high_regime base conditions hold (regime>=11
                    # AND cum3>=0), skip the soft gates C and D. Mechanism:
                    # compound agreement is stronger conviction than either
                    # trigger alone — compound trades today were 3W/0L
                    # (+$4.50 / TP1 in all 3). The hard gates (A: dev<1,
                    # B: peak>=1500+vs<0.30) are structural and stay.
                    _cb_cum3 = m1_features.get("1m_cum_3min_pct")
                    _cb_compound_ok = (
                        _cb_regime is not None
                        and float(_cb_regime) >= 11
                        and _cb_cum3 is not None
                        and float(_cb_cum3) >= 0
                    )
                    if _cb_dev is not None and float(_cb_dev) < 1.0:
                        _cb_gated = True
                        _cb_gate_reason = (
                            f"dev_pct={float(_cb_dev):.1f}%<1.0 "
                            f"(creator dumped 99%+)"
                        )
                    elif (_cb_peak >= 1500
                          and _cb_vs is not None
                          and float(_cb_vs) < 0.30):
                        _cb_gated = True
                        _cb_gate_reason = (
                            f"peak={_cb_peak:.0f}%>=1500 AND "
                            f"vol_spike={float(_cb_vs):.2f}<0.30 "
                            f"(post-pump dead vol)"
                        )
                    elif _cb_compound_ok:
                        # Compound agreement override — skip soft gates C/D.
                        pass
                    elif (_cb_chart_conf is not None
                          and float(_cb_chart_conf) >= 80):
                        _cb_gated = True
                        _cb_gate_reason = (
                            f"chart_pattern_5m_conf={float(_cb_chart_conf):.0f}"
                            f">=80 (textbook bull pattern in distribution = bull-trap)"
                        )
                    elif (_cb_regime is not None
                          and float(_cb_regime) < 10):
                        _cb_gated = True
                        _cb_gate_reason = (
                            f"regime_dip_breadth={float(_cb_regime):.1f}<10 "
                            f"(market lacks dip coordination)"
                        )
                except Exception as _e:
                    logger.debug(f"[DipScanner] clean_break gate err: {_e}")
                if _cb_gated:
                    logger.info(
                        f"[DipScanner] clean_break SUPPRESSED: "
                        f"{token_symbol} {_cb_gate_reason}"
                    )

            # Determine effective entry decision: enter if ANY trigger fires
            _triggers_fired = []
            if _filter_clean_break_verdict == "PASS" and not _cb_gated:
                _triggers_fired.append("clean_break")
            if _trigger_4combo_match:
                _triggers_fired.append("4combo")
            if _trigger_quietpop_match:
                _triggers_fired.append("quiet_pop")
            if _trigger_deepbreakout_match:
                _triggers_fired.append("deep_breakout")
            if _trigger_capitv_match:
                _triggers_fired.append("capit_v")
            if _trigger_engulflow_match:
                _triggers_fired.append("engulf_low")
            if _trigger_hc46_match:
                _triggers_fired.append("hc4_6pct")
            if _trigger_coillong_match:
                _triggers_fired.append("coil_long")
            if _trigger_decay4_match:
                _triggers_fired.append("range_decay_4bar")
            if _trigger_decay4of5_match:
                _triggers_fired.append("range_decay_4of5")
            if _trigger_coiltv_match:
                _triggers_fired.append("coil_top_vol")
            if _trigger_high_regime_match:
                _triggers_fired.append("high_regime")
            if _trigger_momentum_continuation_match:
                _triggers_fired.append("momentum_continuation")
            if _trigger_explosive_break_match:
                _triggers_fired.append("explosive_break")
            if _trigger_range_expansion_qualified_match:
                _triggers_fired.append("range_expansion_qualified")
            if _trigger_6of7_green_vol_match:
                _triggers_fired.append("6of7_green_vol")
            if _trigger_hh10_strict_vol_match:
                _triggers_fired.append("hh10_strict_vol")
            if _trigger_hh10_8plus_match:
                _triggers_fired.append("hh10_8plus")
            if _trigger_vol_velocity_2grn_match:
                _triggers_fired.append("vol_velocity_2grn")

            if not _triggers_fired:
                logger.info(
                    f"[DipScanner] BLOCKED by all triggers: "
                    f"{token_symbol} cb_reasons={','.join(_filter_clean_break_block_reasons)}"
                )
                continue

            _trigger_source = "_".join(_triggers_fired) if len(_triggers_fired) > 1 else _triggers_fired[0]
            if "clean_break" not in _triggers_fired:
                # Logged when an alternative trigger fires while clean_break was BLOCKed
                _alt_reasons = []
                if _trigger_4combo_match:
                    _alt_reasons.extend(_trigger_4combo_reasons)
                if _trigger_quietpop_match:
                    _alt_reasons.extend(_trigger_quietpop_reasons)
                if _trigger_deepbreakout_match:
                    _alt_reasons.extend(_trigger_deepbreakout_reasons)
                if _trigger_capitv_match:
                    _alt_reasons.extend(_trigger_capitv_reasons)
                if _trigger_engulflow_match:
                    _alt_reasons.extend(_trigger_engulflow_reasons)
                if _trigger_hc46_match:
                    _alt_reasons.extend(_trigger_hc46_reasons)
                if _trigger_coillong_match:
                    _alt_reasons.extend(_trigger_coillong_reasons)
                if _trigger_decay4_match:
                    _alt_reasons.extend(_trigger_decay4_reasons)
                if _trigger_decay4of5_match:
                    _alt_reasons.extend(_trigger_decay4of5_reasons)
                if _trigger_coiltv_match:
                    _alt_reasons.extend(_trigger_coiltv_reasons)
                if _trigger_high_regime_match:
                    _alt_reasons.extend(_trigger_high_regime_reasons)
                if _trigger_momentum_continuation_match:
                    _alt_reasons.extend(_trigger_momentum_continuation_reasons)
                if _trigger_explosive_break_match:
                    _alt_reasons.extend(_trigger_explosive_break_reasons)
                if _trigger_range_expansion_qualified_match:
                    _alt_reasons.extend(_trigger_range_expansion_qualified_reasons)
                if _trigger_6of7_green_vol_match:
                    _alt_reasons.extend(_trigger_6of7_green_vol_reasons)
                if _trigger_hh10_strict_vol_match:
                    _alt_reasons.extend(_trigger_hh10_strict_vol_reasons)
                if _trigger_hh10_8plus_match:
                    _alt_reasons.extend(_trigger_hh10_8plus_reasons)
                if _trigger_vol_velocity_2grn_match:
                    _alt_reasons.extend(_trigger_vol_velocity_2grn_reasons)
                logger.info(
                    f"[DipScanner] ENTRY via {_trigger_source} (clean_break BLOCKed): "
                    f"{token_symbol} {','.join(_alt_reasons)}"
                )
            c[f"trigger_source_{_trigger_source}"] = c.get(
                f"trigger_source_{_trigger_source}", 0
            ) + 1

            # ── filter_double_bear — ENFORCED 2026-05-06 PM ────────────────────
            # Secondary gate after clean_break. Block when BOTH bearish-context
            # signals stack: bs_m5 < 0.70 (5m seller-dominant) AND
            # pct_in_1h_range < 0.10 (knife-catch at absolute bottom of 1h
            # range). Either alone is fine; the AND is the discriminator.
            #
            # Trigger: Apple 10:05 buy 2026-05-06 — clean_break PASS by skin
            # of teeth (1m_last_close=+0.22%, exactly the minimum threshold)
            # but every contextual signal screamed "still in downtrend":
            # bs_m5=0.57, p1h=0.058, 1m_volume_spike=0.39, cum3=-2.57. Stop -12%.
            #
            # Among 4 known clean_break PASS trades, this gate blocks ONLY
            # the bad Apple — all 3 winners cleared both thresholds wide
            # (bs_m5 0.94/1.21/1.74; p1h 0.29/0.43/0.45).
            # Lifetime held-out test (n=57): zero fires, zero impact on
            # WR/total — this rule is conceptually targeted at "stacked
            # bearish context dressed up by a 0.2% green wisp."
            #
            # Fail-open if either feature missing.
            _db_bs_m5 = None
            try:
                _db_bs_m5 = float(ratio_m5) if ratio_m5 != float("inf") else None
            except Exception:
                _db_bs_m5 = None
            _db_p1h = range_features.get("pct_in_1h_range")
            _filter_double_bear_block_reasons: list = []
            if (
                _db_bs_m5 is not None
                and _db_p1h is not None
                and _db_bs_m5 < 0.70
                and _db_p1h < 0.10
            ):
                _filter_double_bear_block_reasons.append(
                    f"bs_m5={_db_bs_m5:.2f}<0.70 AND "
                    f"p1h_rng={_db_p1h:.3f}<0.10 (stacked bearish context)"
                )
            _filter_double_bear_verdict = (
                "BLOCK" if _filter_double_bear_block_reasons else "PASS"
            )
            c[f"filter_double_bear_{_filter_double_bear_verdict.lower()}"] = c.get(
                f"filter_double_bear_{_filter_double_bear_verdict.lower()}", 0
            ) + 1
            if _filter_double_bear_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_double_bear: {token_symbol} "
                    f"reasons={','.join(_filter_double_bear_block_reasons)}"
                )
                continue

            # ── filter_seller_dominant — ENFORCED 2026-05-06 PM ───────────────
            # Single-axis gate after clean_break: block when 5m orderflow is
            # strongly seller-dominant (bs_m5 < 0.50). Different pattern from
            # double_bear (which requires BOTH bs and p1h to be bad).
            #
            # Triggers in live data: Apple 10:57:58 buy (bs=0.33, lost -$3.26)
            # and GME 14:11:20 buy (bs=0.43, lost -$3.68). Both passed
            # clean_break and double_bear (p1h was mid-range), but the 5m
            # orderflow was clearly seller-dominant — sellers winning the
            # tug-of-war even though price was holding mid-range. That ends
            # in failure most of the time.
            #
            # Held-out 70/30 validation against the latest dataset:
            # baseline cb alone TEST n=66 WR=71% +$21.50 → cb+seller_dominant
            # TEST n=58 WR=72% +$23.91. Net +$2.41 P&L gain, +1pp WR, -8
            # trades volume.
            #
            # Threshold 0.50 chosen because lifetime winners had bs_m5 >= 0.61
            # at minimum; 0.50 gives a 0.11-buffer and tolerates moderate
            # seller pressure that still resolves into a bounce. Tighter
            # thresholds (0.60+) cut held-out winners.
            #
            # Fail-open if bs_m5 missing.
            _sd_bs_m5 = None
            try:
                _sd_bs_m5 = float(ratio_m5) if ratio_m5 != float("inf") else None
            except Exception:
                _sd_bs_m5 = None
            _filter_seller_dominant_block_reasons: list = []
            if _sd_bs_m5 is not None and _sd_bs_m5 < 0.50:
                _filter_seller_dominant_block_reasons.append(
                    f"bs_m5={_sd_bs_m5:.2f}<0.50 (5m sellers dominating order flow)"
                )
            _filter_seller_dominant_verdict = (
                "BLOCK" if _filter_seller_dominant_block_reasons else "PASS"
            )
            c[f"filter_seller_dominant_{_filter_seller_dominant_verdict.lower()}"] = c.get(
                f"filter_seller_dominant_{_filter_seller_dominant_verdict.lower()}", 0
            ) + 1
            # 2026-05-07: DEMOTED to SHADOW. Live phantom forward test (55
            # snapshots, ~2 days) showed -$5.04 lift on T_clean_break_only vs
            # S_live_prod_stack — opposite direction from the original held-out
            # +$2.41 lift study. Direction-flip is symptomatic of overfit or
            # regime change. Demote to shadow to log without enforcing while
            # we collect more forward data.
            if _filter_seller_dominant_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_seller_dominant SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_seller_dominant_block_reasons)}"
                )

            # ── Multi-timeframe momentum stacking (shadow, 2026-05-05) ────────
            # Hypothesis: "textbook pullback resolving" = 15m red + 5m red +
            # 1m green. Different from filter_fake_bounce because it requires
            # macro/meso DOWN (real pullback context), not just micro UP.
            # Pure derivation, no extra fetches.
            _mtf_green_count = 0
            _mtf_vol_align = 0
            _mtf_textbook = 0
            try:
                _cs1_lf = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                _cs5_lf = _chart_data.candles_5m if _chart_data and _chart_data.candles_5m else []
                _cs15_lf = _chart_data.candles_15m if _chart_data and _chart_data.candles_15m else []
                _last1 = _cs1_lf[-1] if _cs1_lf else None
                _last5 = _cs5_lf[-1] if _cs5_lf else None
                _last15 = _cs15_lf[-1] if _cs15_lf else None
                # Green flags (close > open)
                _g1 = bool(_last1 and _last1.close > _last1.open)
                _g5 = bool(_last5 and _last5.close > _last5.open)
                _g15 = bool(_last15 and _last15.close > _last15.open)
                _mtf_green_count = int(_g1) + int(_g5) + int(_g15)
                # Volume-spike flags (last vol > avg of prior, ratio > 1.0)
                def _vs(series):
                    if not series or len(series) < 4:
                        return False
                    prior = [k.volume for k in series[-5:-1]]
                    if not prior:
                        return False
                    avg = sum(prior) / len(prior)
                    return avg > 0 and series[-1].volume / avg > 1.0
                _mtf_vol_align = int(_vs(_cs1_lf)) + int(_vs(_cs5_lf)) + int(_vs(_cs15_lf))
                # Textbook pullback resolving: 15m red AND 5m red AND 1m green
                _mtf_textbook = 1 if (
                    _last15 is not None and _last15.close < _last15.open
                    and _last5 is not None and _last5.close < _last5.open
                    and _last1 is not None and _last1.close > _last1.open
                ) else 0
            except Exception as _e:
                logger.debug(f"[DipScanner] mtf calc err: {_e}")

            # ── Macro-window features — SHADOW 2026-05-06 PM ───────────────────
            # Derived from existing 1m candles (no extra fetches). Used to test
            # the "buy capitulation, not topping" hypothesis from multi-token
            # chart analysis (n=254 dips across 21 tokens):
            #   - macro30_pct < -10 → 52% WR (+4.5pp lift over 48% baseline)
            #   - macro60<-30 AND macro30<-15 → 59% WR (+11pp, n=35)
            #   - macro60>50 (uptrend bias) → 29% WR (-18pp — worst filter)
            # Shadow only: record values, no enforcement. After ~3-7 days of
            # forward data we can decide if a filter on macro30 has real lift.
            _macro30_pct = None
            _macro60_pct = None
            # Token-quality features — SHADOW 2026-05-06 PM. From multi-token
            # analysis (n=854 simulated entries, 21 tokens), the strongest
            # winner-vs-loser separators were TOKEN-LEVEL features over the
            # 60m window, not entry-level features:
            #   - p90_body_pct (winners 3.7% / losers 2.2%) — losers are too
            #     flat for the bot to hit TP1 (+8.7%). MEGR/ROAF/LOL all
            #     scored 0% WR with all flats because price never moved enough.
            #   - buyvol_ratio_60m (winners 1.87 / losers 0.75) — winners
            #     have buy-side volume dominance over the full 60m window
            # Both fail-open if 1m candles missing.
            _chart_p90_body_pct = None
            _chart_buyvol_ratio_60m = None
            try:
                _cs1m = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if len(_cs1m) >= 31 and _cs1m[-1].close > 0 and _cs1m[-31].close > 0:
                    _macro30_pct = (_cs1m[-1].close / _cs1m[-31].close - 1) * 100
                if len(_cs1m) >= 61 and _cs1m[-1].close > 0 and _cs1m[-61].close > 0:
                    _macro60_pct = (_cs1m[-1].close / _cs1m[-61].close - 1) * 100
                # Use the last 60 candles (or fewer if not available) for
                # p90 body and buyvol ratio.
                _w60 = _cs1m[-60:] if len(_cs1m) >= 60 else _cs1m
                if len(_w60) >= 20:
                    _bodies = [
                        abs(b.close / b.open - 1) * 100
                        for b in _w60 if b.open > 0
                    ]
                    if _bodies:
                        _bodies_sorted = sorted(_bodies)
                        _p90_idx = int(len(_bodies_sorted) * 0.9)
                        _chart_p90_body_pct = _bodies_sorted[
                            min(_p90_idx, len(_bodies_sorted) - 1)
                        ]
                    _gv = sum(b.volume for b in _w60 if b.close > b.open)
                    _rv = sum(b.volume for b in _w60 if b.close < b.open)
                    if _rv > 0:
                        _chart_buyvol_ratio_60m = _gv / _rv
                    elif _gv > 0:
                        _chart_buyvol_ratio_60m = 999.0
            except Exception as _e:
                logger.debug(f"[DipScanner] chart-quality calc err: {_e}")

            # ── filter_low_volatility — SHADOW 2026-05-06 PM ──────────────────
            # Record-only verdict for the "dead token" pattern: when
            # chart_p90_body_pct < 1.0, the token's biggest 1m candles in
            # the last hour barely moved 1% — making the bot's TP1 (+8.7%)
            # essentially unreachable within our 60-min resolution window.
            #
            # Multi-token simulation evidence (n=854 entries across 21
            # tokens): MEGR (p90_body 0.6%, 79 entries, 0% WR all flats),
            # ROAF (p90 0.1%, 56 entries, all flats), LOL (p90 1.0%, 26
            # entries, all flats) — collectively 161 entries that the bot
            # COULD NOT WIN on. Winning tokens averaged p90_body 3.7%.
            #
            # Shadow only — record verdict, no enforcement. After forward
            # data accumulates (~30+ real trades) we can decide whether
            # to promote to enforced.
            #
            # Fail-open if chart_p90_body_pct missing.
            _filter_low_vol_block_reasons: list = []
            if (
                _chart_p90_body_pct is not None
                and _chart_p90_body_pct < 1.0
            ):
                _filter_low_vol_block_reasons.append(
                    f"p90_body={_chart_p90_body_pct:.2f}%<1.0 "
                    f"(token too flat — TP1 essentially unreachable)"
                )
            _filter_low_vol_verdict = (
                "BLOCK" if _filter_low_vol_block_reasons else "PASS"
            )
            c[f"filter_low_volatility_{_filter_low_vol_verdict.lower()}"] = c.get(
                f"filter_low_volatility_{_filter_low_vol_verdict.lower()}", 0
            ) + 1
            if _filter_low_vol_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_low_volatility SHADOW would-block: "
                    f"{token_symbol} reasons={','.join(_filter_low_vol_block_reasons)}"
                )

            # ── filter_topping — SHADOW 2026-05-06 PM ────────────────────────
            # Record-only verdict for the "you're catching a top" pattern:
            # BLOCK when macro30_pct > +5% (price already up >5% over the
            # last 30m at signal time). Catches knife-catches at fresh peaks.
            #
            # Multi-token simulation evidence (n=2592 entries across 58
            # token-batches):
            #   - macro30 > +10%: 52% WR, -0.62%/trade
            #   - macro30 +5 to +10: 47% WR, -0.85%/trade ← worst single bucket
            #   - macro30 -5 to 0:  57% WR, +0.38%/trade
            #   - macro30 < -15:    60% WR, +1.13%/trade
            #
            # Threshold +5 picked because the +5 to +10 bucket is the
            # worst-performing zone — flat-but-slightly-up is paradoxically
            # worse than full topping. Blocking at +5 cuts both the
            # "distribution top" (>+10) and "weak top continuation" (+5 to +10)
            # patterns without touching the productive negative-macro zone.
            #
            # Shadow only — record verdict, no enforcement. Fail-open if
            # macro30_pct missing.
            _filter_topping_block_reasons: list = []
            if _macro30_pct is not None and _macro30_pct > 5:
                _filter_topping_block_reasons.append(
                    f"macro30={_macro30_pct:+.1f}%>+5 "
                    f"(price already up >5% in 30m — knife-catch zone)"
                )
            _filter_topping_verdict = (
                "BLOCK" if _filter_topping_block_reasons else "PASS"
            )
            c[f"filter_topping_{_filter_topping_verdict.lower()}"] = c.get(
                f"filter_topping_{_filter_topping_verdict.lower()}", 0
            ) + 1
            if _filter_topping_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_topping SHADOW would-block: "
                    f"{token_symbol} reasons={','.join(_filter_topping_block_reasons)}"
                )

            # ── filter_wide_range_entry — SHADOW 2026-05-06 PM ────────────────
            # Record-only verdict for "panicky volatility candle" pattern:
            # BLOCK when the entry 1m candle's full range (high - low) exceeds
            # 3% of open. These are wide-range candles where buyers and
            # sellers are fighting in a big band — often resolves into
            # reversal as one side caves.
            #
            # Validated through scripts/validate_filter.py:
            #   - Sim (n=5903): PASS-cohort lift +0.115%/trade, +0.4pp WR
            #   - Retro on n=10 real bot trades: would block 1W/1L, delta
            #     +$1.06 (the L was GME 14:11 -$3.68 — the meatiest loss)
            # Different mechanism from filter_wick_dominant (which validator
            # rejected): wick_dominant looks at asymmetric wick vs body,
            # this looks at total range size.
            #
            # Shadow only — record verdict, no enforcement.
            # Fail-open if entry candle missing.
            _wre_range_pct = None
            try:
                _cs1m_wre = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                if _cs1m_wre and _cs1m_wre[-1].open > 0:
                    _last_cdl = _cs1m_wre[-1]
                    _wre_range_pct = (
                        (_last_cdl.high - _last_cdl.low) / _last_cdl.open * 100
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] wide_range calc err: {_e}")
            _filter_wide_range_block_reasons: list = []
            if _wre_range_pct is not None and _wre_range_pct > 3.0:
                _filter_wide_range_block_reasons.append(
                    f"entry_range={_wre_range_pct:.2f}%>3.0 "
                    f"(panicky-volatility candle — likely reversal)"
                )
            _filter_wide_range_verdict = (
                "BLOCK" if _filter_wide_range_block_reasons else "PASS"
            )
            c[f"filter_wide_range_entry_{_filter_wide_range_verdict.lower()}"] = c.get(
                f"filter_wide_range_entry_{_filter_wide_range_verdict.lower()}", 0
            ) + 1
            if _filter_wide_range_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_wide_range_entry SHADOW would-block: "
                    f"{token_symbol} reasons={','.join(_filter_wide_range_block_reasons)}"
                )

            # ── filter_double_bottom — SHADOW 2026-05-06 PM ───────────────────
            # Record-only verdict for "rock-bottom in both 5m and 1h"
            # pattern: BLOCK when pct_in_5m_range < 0.10 AND
            # pct_in_1h_range < 0.10. Catches knife-catching at the
            # absolute floor of BOTH micro and macro perspectives.
            #
            # Targets the PAYmo 12:44 stop-out (-$2.71) which slipped
            # past every other filter (bs_m5=3.00 buyer-dominant, but
            # p1h=0.009 / p5m=0.039 — rock bottom in both timeframes).
            #
            # Validated through scripts/validate_filter.py:
            #   - Retro on real bot trades: blocks PAYmo, +$2.71 delta.
            #   - Lifetime TRAIN (n=401): blocks 35 trades, sum -$36
            #     ($-1.03/trade — clearly bad cohort). Lift +$0.048/trade.
            #   - Lifetime TEST (n=173): blocks 15 trades, sum -$9.68
            #     ($-0.65/trade). Lift +$0.055/trade. Both directions agree.
            #
            # Different from filter_double_bear (bs_m5+p1h) — this uses
            # p5m+p1h for a different orderflow profile.
            #
            # Shadow only — record verdict, no enforcement. Fail-open if
            # either pct_in_*_range feature missing.
            _db2_p5m = range_features.get("pct_in_5m_range")
            _db2_p1h = range_features.get("pct_in_1h_range")
            _filter_double_bottom_block_reasons: list = []
            if (
                _db2_p5m is not None
                and _db2_p1h is not None
                and _db2_p5m < 0.10
                and _db2_p1h < 0.10
            ):
                _filter_double_bottom_block_reasons.append(
                    f"p5m={_db2_p5m:.3f}<0.10 AND p1h={_db2_p1h:.3f}<0.10 "
                    f"(rock-bottom in both timeframes)"
                )
            _filter_double_bottom_verdict = (
                "BLOCK" if _filter_double_bottom_block_reasons else "PASS"
            )
            c[f"filter_double_bottom_{_filter_double_bottom_verdict.lower()}"] = c.get(
                f"filter_double_bottom_{_filter_double_bottom_verdict.lower()}", 0
            ) + 1
            if _filter_double_bottom_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_double_bottom SHADOW would-block: "
                    f"{token_symbol} reasons={','.join(_filter_double_bottom_block_reasons)}"
                )

            # ── filter_stairstep — SHADOW 2026-05-07 PM ───────────────────────
            # Detect "managed pump" stairstep: 3+ large green 5m candles
            # (body >= 3%) separated by 12+ near-flat bars (|body| < 1%) in
            # the last 24 5m bars (2h window), where the green-jump bodies
            # account for >= 70% of the total 24-bar range.
            #
            # Mechanism: coordinated buy events fired at intervals create
            # discrete price levels rather than organic continuous demand.
            # When the pump artist steps away, bid evaporates and price
            # falls back through the steps. Trigger case: WCOR 15:11
            # 2026-05-07 (5m chart showed 4 stair-jumps over ~12h).
            #
            # Validation: master bar dataset (n=295 tokens, 45k 5m windows).
            # Tag rate 1.9%. Stair forward 60m: -1.68%/trade. Non-stair:
            # +1.29%/trade. Lift +2.96%/trade on tagged set.
            #
            # Caveat: validated on raw 60-min forward returns, NOT bot's
            # actual TP/SL/trail outcomes. Shadow-only until ≥10 forward
            # tagged real trades confirm the lift on actual P&L.
            #
            # Fail-open if 5m bars unavailable.
            _filter_stairstep_block_reasons: list = []
            try:
                _ss_cs5 = (
                    _chart_data.candles_5m
                    if _chart_data and _chart_data.candles_5m
                    else []
                )
                _SS_LOOKBACK = 24
                _SS_JUMP = 3.0
                _SS_FLAT = 1.0
                _SS_MIN_JUMPS = 3
                _SS_MIN_FLATS = 12
                _SS_JUMP_SHARE = 0.7
                if len(_ss_cs5) >= _SS_LOOKBACK:
                    _ss_window = _ss_cs5[-_SS_LOOKBACK:]
                    _ss_bodies = []
                    _ss_bad = False
                    for _b in _ss_window:
                        if _b.open is None or _b.open <= 0:
                            _ss_bad = True
                            break
                        _ss_bodies.append((_b.close - _b.open) / _b.open * 100)
                    if not _ss_bad:
                        _ss_n_jumps = sum(1 for bp in _ss_bodies if bp >= _SS_JUMP)
                        _ss_n_flat = sum(1 for bp in _ss_bodies if abs(bp) < _SS_FLAT)
                        _ss_high = max(b.high for b in _ss_window)
                        _ss_low = min(b.low for b in _ss_window)
                        _ss_range_pct = (
                            (_ss_high - _ss_low) / _ss_low * 100
                            if _ss_low > 0 else 0
                        )
                        _ss_jump_total = sum(bp for bp in _ss_bodies if bp >= _SS_JUMP)
                        if (
                            _ss_n_jumps >= _SS_MIN_JUMPS
                            and _ss_n_flat >= _SS_MIN_FLATS
                            and (
                                _ss_range_pct == 0
                                or _ss_jump_total >= _ss_range_pct * _SS_JUMP_SHARE
                            )
                        ):
                            _filter_stairstep_block_reasons.append(
                                f"5m_stairstep: {_ss_n_jumps} green-jumps>="
                                f"{_SS_JUMP}% + {_ss_n_flat} flat-bars<{_SS_FLAT}% "
                                f"in last {_SS_LOOKBACK} bars "
                                f"(jump_total={_ss_jump_total:.1f}%/range={_ss_range_pct:.1f}%)"
                            )
            except Exception as _e:
                logger.debug(f"[DipScanner] stairstep calc err: {_e}")
            _filter_stairstep_verdict = (
                "BLOCK" if _filter_stairstep_block_reasons else "PASS"
            )
            c[f"filter_stairstep_{_filter_stairstep_verdict.lower()}"] = c.get(
                f"filter_stairstep_{_filter_stairstep_verdict.lower()}", 0
            ) + 1
            if _filter_stairstep_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_stairstep SHADOW would-block: "
                    f"{token_symbol} reasons={','.join(_filter_stairstep_block_reasons)}"
                )

            # ── filter_seller_imbalance — SHADOW 2026-05-07 PM ────────────────
            # Detect "sellers winning the 5m net flow" via tier3
            # net_flow_5m_imbalance = (signed_vol) / (gross_vol).
            # Range -1 to +1; negative = sellers dominating dollar flow.
            #
            # BLOCK when net_flow_5m_imbalance < -0.2 (~deep seller dominance,
            # not just slight sell tilt).
            #
            # Validation (n=681 lifetime, n=269 since 2026-05-04):
            #   - Lifetime: blocked n=84 avg=$+0.05, kept n=597 avg=$+0.42
            #     lift=+$0.036/trade
            #   - Recent:   blocked n=36 avg=$-0.26, kept n=233 avg=$-0.13
            #     lift=+$0.017/trade
            #   - Sign agreement YES, top-10 winner regression: 0/10
            #
            # Trigger case: ZEREBRO 15:47 (net_flow_5m_imbalance=-0.636) —
            # caught the deepest distribution-buy of the day. Note the OTHER
            # ZEREBRO at 16:00 had net_flow=+0.10 and would NOT be blocked,
            # which matches the filter's targeted scope (not a blanket veto).
            #
            # Shadow only — small absolute lift (~$0.02/trade), narrow tag rate
            # (~10% recent, ~12% lifetime). Promote if forward data confirms.
            #
            # Fail-open if net_flow_5m_imbalance missing from tier3 features.
            _filter_seller_imbalance_block_reasons: list = []
            try:
                _sni = _tier3_features.get("net_flow_5m_imbalance")
                if _sni is not None and float(_sni) < -0.2:
                    _filter_seller_imbalance_block_reasons.append(
                        f"net_flow_5m_imbalance={float(_sni):+.3f}<-0.2 "
                        f"(sellers dominating 5m dollar flow)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] seller_imbalance calc err: {_e}")
            _filter_seller_imbalance_verdict = (
                "BLOCK" if _filter_seller_imbalance_block_reasons else "PASS"
            )
            c[f"filter_seller_imbalance_{_filter_seller_imbalance_verdict.lower()}"] = c.get(
                f"filter_seller_imbalance_{_filter_seller_imbalance_verdict.lower()}", 0
            ) + 1
            if _filter_seller_imbalance_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_seller_imbalance SHADOW would-block: "
                    f"{token_symbol} reasons={','.join(_filter_seller_imbalance_block_reasons)}"
                )

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
                # filter_round_trip — enforced 90m round-trip distribution gate.
                "filter_round_trip_verdict": _filter_round_trip_verdict,
                "filter_round_trip_block_reasons": _filter_round_trip_block_reasons,
                # filter_vp_poc — enforced "entry above POC" gate.
                "filter_vp_poc_verdict": _filter_vp_poc_verdict,
                "filter_vp_poc_block_reasons": _filter_vp_poc_block_reasons,
                # filter_turn — DOWNGRADED to shadow 2026-05-05 PM.
                "filter_turn_verdict": _filter_turn_verdict,
                "filter_turn_block_reasons": _filter_turn_block_reasons,
                # filter_confirmation_candle — SHADOW timing fix 2026-05-05 PM.
                "filter_confirmation_candle_verdict": _filter_confirm_verdict,
                "filter_confirmation_candle_block_reasons": _filter_confirm_block_reasons,
                # filter_clean_break — ENFORCED 2026-05-06 (held-out +13pp lift).
                "filter_clean_break_verdict": _filter_clean_break_verdict,
                "filter_clean_break_block_reasons": _filter_clean_break_block_reasons,
                # 4-combo parallel trigger — ENFORCED 2026-05-06 PM.
                # trigger_source: which trigger fired (or _-joined for multi).
                "trigger_source": _trigger_source,
                "trigger_4combo_match": _trigger_4combo_match,
                "trigger_4combo_reasons": _trigger_4combo_reasons,
                # quiet_pop_breakout parallel trigger — ENFORCED 2026-05-06 PM.
                "trigger_quietpop_match": _trigger_quietpop_match,
                "trigger_quietpop_reasons": _trigger_quietpop_reasons,
                # deep_breakout_volume parallel trigger — ENFORCED 2026-05-06.
                "trigger_deepbreakout_match": _trigger_deepbreakout_match,
                "trigger_deepbreakout_reasons": _trigger_deepbreakout_reasons,
                # capitulation_v parallel trigger — ENFORCED 2026-05-06 (V-bottom catch).
                "trigger_capitv_match": _trigger_capitv_match,
                "trigger_capitv_reasons": _trigger_capitv_reasons,
                # engulf_at_low parallel trigger — ENFORCED 2026-05-06 (engulf+breakout).
                "trigger_engulflow_match": _trigger_engulflow_match,
                "trigger_engulflow_reasons": _trigger_engulflow_reasons,
                # hc4_6pct parallel trigger — ENFORCED 2026-05-07 (HC class champion).
                "trigger_hc46_match": _trigger_hc46_match,
                "trigger_hc46_reasons": _trigger_hc46_reasons,
                # coil_long parallel trigger — ENFORCED 2026-05-07 (8th, orthogonal coil-release).
                "trigger_coillong_match": _trigger_coillong_match,
                "trigger_coillong_reasons": _trigger_coillong_reasons,
                # range_decay_4bar parallel trigger — ENFORCED 2026-05-07 (9th, compression climax).
                "trigger_decay4_match": _trigger_decay4_match,
                "trigger_decay4_reasons": _trigger_decay4_reasons,
                # range_decay_4of5 parallel trigger — ENFORCED 2026-05-07 (10th, looser compression).
                "trigger_decay4of5_match": _trigger_decay4of5_match,
                "trigger_decay4of5_reasons": _trigger_decay4of5_reasons,
                # coil_top_vol parallel trigger — ENFORCED 2026-05-07 (11th, coil + vol-confirmed).
                "trigger_coiltv_match": _trigger_coiltv_match,
                "trigger_coiltv_reasons": _trigger_coiltv_reasons,
                # decay_5bar parallel trigger — SHADOW 2026-05-07 (gathering forward retro on 87.5% WR signal).
                "trigger_decay5_match": _trigger_decay5_match,
                "trigger_decay5_reasons": _trigger_decay5_reasons,
                # high_regime parallel trigger — ENFORCED 2026-05-07 PM (12th, regime+momentum gate).
                "trigger_high_regime_match": _trigger_high_regime_match,
                "trigger_high_regime_reasons": _trigger_high_regime_reasons,
                # momentum_continuation parallel trigger — ENFORCED 2026-05-07 PM (13th, fast-mover continuation).
                "trigger_momentum_continuation_match": _trigger_momentum_continuation_match,
                "trigger_momentum_continuation_reasons": _trigger_momentum_continuation_reasons,
                # explosive_break parallel trigger — ENFORCED 2026-05-07 PM (14th, multi-TF explosive).
                "trigger_explosive_break_match": _trigger_explosive_break_match,
                "trigger_explosive_break_reasons": _trigger_explosive_break_reasons,
                # range_expansion_qualified parallel trigger — ENFORCED 2026-05-07 PM (15th, single-TF deep expansion).
                "trigger_range_expansion_qualified_match": _trigger_range_expansion_qualified_match,
                "trigger_range_expansion_qualified_reasons": _trigger_range_expansion_qualified_reasons,
                # 6of7_green_vol parallel trigger — ENFORCED 2026-05-07 PM (16th, mostly-green sequence).
                "trigger_6of7_green_vol_match": _trigger_6of7_green_vol_match,
                "trigger_6of7_green_vol_reasons": _trigger_6of7_green_vol_reasons,
                # hh10_strict_vol parallel trigger — ENFORCED 2026-05-07 PM (17th, HH-based trend strength).
                "trigger_hh10_strict_vol_match": _trigger_hh10_strict_vol_match,
                "trigger_hh10_strict_vol_reasons": _trigger_hh10_strict_vol_reasons,
                # hh10_8plus parallel trigger — ENFORCED 2026-05-07 PM (18th, pure HH-trend no vol gate).
                "trigger_hh10_8plus_match": _trigger_hh10_8plus_match,
                "trigger_hh10_8plus_reasons": _trigger_hh10_8plus_reasons,
                # vol_velocity_2grn parallel trigger — ENFORCED 2026-05-07 PM (19th, vol-velocity gap-mined).
                "trigger_vol_velocity_2grn_match": _trigger_vol_velocity_2grn_match,
                "trigger_vol_velocity_2grn_reasons": _trigger_vol_velocity_2grn_reasons,
                # squeeze_pullback parallel trigger — SHADOW 2026-05-06 (gathering retro).
                "trigger_squeeze_match": _trigger_squeeze_match,
                "trigger_squeeze_reasons": _trigger_squeeze_reasons,
                # filter_double_bear — ENFORCED 2026-05-06 PM (zero-harm Apple gate).
                "filter_double_bear_verdict": _filter_double_bear_verdict,
                "filter_double_bear_block_reasons": _filter_double_bear_block_reasons,
                # filter_seller_dominant — ENFORCED 2026-05-06 PM (held-out +$2.41 lift).
                "filter_seller_dominant_verdict": _filter_seller_dominant_verdict,
                "filter_seller_dominant_block_reasons": _filter_seller_dominant_block_reasons,
                # Macro-window shadow features 2026-05-06 PM. Hypothesis: macro30<-10
                # → +4.5pp WR lift; capitulation (macro60<-30 AND macro30<-15) → +11pp.
                "macro30_pct": _macro30_pct,
                "macro60_pct": _macro60_pct,
                # Token-quality shadow features 2026-05-06 PM. Hypothesis:
                # p90_body<1.0 → death-by-flat (MEGR/ROAF/LOL pattern, 0% WR);
                # buyvol_ratio>1.0 separates winning tokens (1.87 mean) from
                # losing tokens (0.75 mean) over the 60m window.
                "chart_p90_body_pct": _chart_p90_body_pct,
                "chart_buyvol_ratio_60m": _chart_buyvol_ratio_60m,
                # filter_low_volatility — SHADOW 2026-05-06 PM (dead-token gate).
                "filter_low_volatility_verdict": _filter_low_vol_verdict,
                "filter_low_volatility_block_reasons": _filter_low_vol_block_reasons,
                # filter_topping — SHADOW 2026-05-06 PM (catch knife-catch at peak).
                "filter_topping_verdict": _filter_topping_verdict,
                "filter_topping_block_reasons": _filter_topping_block_reasons,
                # filter_wide_range_entry — SHADOW 2026-05-06 PM (volatility-candle gate).
                "filter_wide_range_entry_verdict": _filter_wide_range_verdict,
                "filter_wide_range_entry_block_reasons": _filter_wide_range_block_reasons,
                "chart_entry_range_pct": _wre_range_pct,
                # filter_double_bottom — SHADOW 2026-05-06 PM (p5m+p1h rock-bottom gate).
                "filter_double_bottom_verdict": _filter_double_bottom_verdict,
                "filter_double_bottom_block_reasons": _filter_double_bottom_block_reasons,
                # filter_stairstep — managed-pump detection (shadow).
                "filter_stairstep_verdict": _filter_stairstep_verdict,
                "filter_stairstep_block_reasons": _filter_stairstep_block_reasons,
                # filter_seller_imbalance — 5m net dollar flow seller-dominance (shadow).
                "filter_seller_imbalance_verdict": _filter_seller_imbalance_verdict,
                "filter_seller_imbalance_block_reasons": _filter_seller_imbalance_block_reasons,
                # 4 SHADOW filters added 2026-05-05 (no enforcement).
                "filter_weak_bounce_verdict": _filter_weak_bounce_verdict,
                "filter_weak_bounce_block_reasons": _filter_weak_bounce_block_reasons,
                "filter_weak_bounce_body_over_range": _filter_weak_bounce_body_over_range,
                "filter_weak_bounce_v2_verdict": _filter_weak_bounce_v2_verdict,
                "filter_weak_bounce_v2_block_reasons": _filter_weak_bounce_v2_block_reasons,
                "filter_slip_asym_verdict": _filter_slip_asym_verdict,
                "filter_slip_asym_block_reasons": _filter_slip_asym_block_reasons,
                "filter_regime_panic_verdict": _filter_regime_panic_verdict,
                "filter_regime_panic_block_reasons": _filter_regime_panic_block_reasons,
                "filter_dev_dumping_verdict": _filter_dev_dumping_verdict,
                "filter_dev_dumping_block_reasons": _filter_dev_dumping_block_reasons,
                # 3 SHADOW filters from regret analysis (2026-05-05 PM).
                "filter_bs_m5_low_verdict": _filter_bs_m5_low_verdict,
                "filter_bs_m5_low_block_reasons": _filter_bs_m5_low_block_reasons,
                "filter_big_trade_size_verdict": _filter_big_trade_size_verdict,
                "filter_big_trade_size_block_reasons": _filter_big_trade_size_block_reasons,
                "filter_stale_watch_verdict": _filter_stale_watch_verdict,
                "filter_stale_watch_block_reasons": _filter_stale_watch_block_reasons,
                # Axiom active-users signal (Task 1 from axiom-full-utilization plan).
                # Captures user_cache value + spike flag at signal-fire time.
                # Spike = current count >= 3x rolling 4-sample baseline.
                "axiom_active_users": (
                    self.axiom_price_feed.user_cache.get(token_address.lower())
                    if self.axiom_price_feed else None
                ),
                "axiom_active_users_baseline": (
                    (lambda h: round(sum(h[:-1]) / max(len(h) - 1, 1), 1) if len(h) >= 2 else None)(
                        self.axiom_price_feed._user_baseline_window.get(token_address.lower(), [])
                    ) if self.axiom_price_feed else None
                ),
                "axiom_active_users_is_spike": (
                    token_address.lower() in self.axiom_price_feed._user_count_spikes
                    if self.axiom_price_feed else False
                ),
                # Multi-timeframe momentum stacking (shadow, 2026-05-05).
                "mtf_green_count": _mtf_green_count,
                "mtf_vol_align": _mtf_vol_align,
                "mtf_textbook_pullback": _mtf_textbook,
                # Jupiter slip time-series (shadow, 2026-05-05).
                **slip_ts_features,
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
                **_trade_log_dict,  # order-size dist + buyer uniqueness / wash + buyer profile
                **_graduation_dict,  # bonding-curve graduation status (pump.fun specific)
                **_tier2_features,  # Tier-2 instrumentation (vwap_1h, pct_off_peak,
                                    # higher_low, rsi/bb, bundle_v2, trade-size shift,
                                    # regime breadth) — shadow only, 2026-05-04.
                **_tier3_features,  # Tier-3 instrumentation (support touches, wick
                                    # ratios, freq derivative, net flow windows,
                                    # hours_since_graduation) — shadow, 2026-05-04.
                **_tier1_features,  # Tier-1 (smart-money score, top makers capture,
                                    # dev wallet pct) — shadow, 2026-05-04.
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
                "vol_m5_zero", "vol_h1_decay", "filter_turn_block",
                "red_h24", "trend_reversal", "top_exhaustion", "no_dip", "h1_mid_dip", "m5_dip_over", "falling_knife", "mega_pump_middle",
                "seller_h1_red_m5", "seller_pump", "no_1m_reversal", "m1_top_tick", "m1_false_bounce", "top_consolidation",
                "bs_h6", "bs_h6_missing", "already_open", "loss_cooldown",
                "obs_high_cycles", "filter_peak_floor_block", "filter_real_dip_3_block",
                "filter_corpse_block", "filter_fake_bounce_block",
                "filter_round_trip_block", "filter_weak_bounce_v2_block",
                "filter_fofar_block",
                "filter_vp_poc_block",
                "filter_two_pattern_block",
                # 4 SHADOW filters added 2026-05-05 — counters only, no enforcement.
                "filter_weak_bounce_block", "filter_slip_asym_block",
                "filter_regime_panic_block", "filter_dev_dumping_block",
                # 3 SHADOW filters from regret analysis (2026-05-05 PM).
                "filter_bs_m5_low_block", "filter_big_trade_size_block",
                "filter_stale_watch_block",
                # Timing fix shadow 2026-05-05 PM.
                "filter_confirmation_candle_block",
                # ENFORCED 2026-05-06 — clean-break user pattern.
                "filter_clean_break_block",
                # ENFORCED 2026-05-06 PM — double-bearish-context gate.
                "filter_double_bear_block",
                # ENFORCED 2026-05-06 PM — bs_m5<0.50 single-axis gate.
                "filter_seller_dominant_block",
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
                import random
                _cycle_terms = random.sample(
                    _SEARCH_TERMS_POOL,
                    min(_SEARCH_TERMS_PER_CYCLE, len(_SEARCH_TERMS_POOL)),
                )
                urls = [
                    "https://api.dexscreener.com/token-boosts/top/v1",
                    "https://api.dexscreener.com/token-profiles/latest/v1",
                ] + [
                    f"https://api.dexscreener.com/latest/dex/search?q={kw}&chainId={_DEX_CHAIN}"
                    for kw in _cycle_terms
                ]

                ds_task = asyncio.gather(*[_get(session, u) for u in urls],
                                         return_exceptions=True)
                gt_task = self.gt_client.fetch_trending_pools(pages=4)
                # Axiom-trending source (2026-05-05) — widens dip-buy
                # candidate pool with tokens Axiom flags as hot. Same
                # DexScreener-style pair format as GT; gets enriched below.
                # Fail-open if no auth (returns empty list).
                # 2026-05-07: switched to time_period="5m" to rotate the
                # candidate pool faster — 1h was producing the same set
                # cycle-over-cycle, contributing to cycles_seen ratchet.
                axiom_auth = self.axiom_price_feed.auth if self.axiom_price_feed else None
                async def _axiom_task():
                    if not axiom_auth:
                        return []
                    try:
                        from feeds.axiom_discovery import fetch_axiom_trending_pairs
                        return await fetch_axiom_trending_pairs(axiom_auth, time_period="5m")
                    except Exception as _e:
                        logger.debug(f"[DipScanner] Axiom trending fetch err: {_e}")
                        return []
                results, gt_pairs, axiom_pairs = await asyncio.gather(
                    ds_task, gt_task, _axiom_task(), return_exceptions=True
                )
                if isinstance(results, Exception):
                    results = []
                if isinstance(gt_pairs, Exception):
                    gt_pairs = []
                if isinstance(axiom_pairs, Exception):
                    axiom_pairs = []

                # Seed GT entries — will be overwritten by DS enrichment below
                # so the final pair dict has txns data for the bs_h6 filter.
                for p in (gt_pairs or []):
                    addr = (p.get("baseToken") or {}).get("address", "")
                    if addr and addr not in pair_by_addr:
                        pair_by_addr[addr] = p
                        source_by_addr[addr] = "gt_trending"

                # Seed Axiom entries — same enrichment treatment as GT.
                for p in (axiom_pairs or []):
                    addr = (p.get("baseToken") or {}).get("address", "")
                    if addr and addr not in pair_by_addr:
                        pair_by_addr[addr] = p
                        source_by_addr[addr] = "axiom_trending"

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
                            elif source_by_addr.get(addr) == "axiom_trending":
                                pair_by_addr[addr] = p
                                source_by_addr[addr] = "axiom_enriched"
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
            "axiom_trending": 0, "axiom_enriched": 0,
        }
        for src in source_by_addr.values():
            source_counts[src] = source_counts.get(src, 0) + 1

        return list(pair_by_addr.values()), source_counts
