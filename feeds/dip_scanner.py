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

# Full-population signal recorder — captures every Signal: emit and its
# outcome (BUY/BLOCK/CONTINUED) to {DATA_DIR}/signal_events.jsonl. Lets
# us mine across BOTH bought and rejected populations, which trades.db
# (executed buys only) cannot. Idempotent install — safe to import twice.
try:
    from core.signal_event_recorder import install as _install_signal_recorder
    _install_signal_recorder(logger)
except Exception as _e:
    logger.warning(f"signal_event_recorder install failed: {_e}")

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
_SCAN_INTERVAL = 30  # seconds between full scan cycles (lowered 90→30 2026-05-15 for 3x throughput)


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
        # Sticky watchlist — keep tokens we've seen on trending feeds for 12h
        # even after they drop off. Solves the BURNIE-class universe gap:
        # 2026-05-14 BURNIE first appeared in scanner at 11:29 CT, missing
        # three +6-11% V-bottom rallies at 04:10/05:25/07:55 CT. With sticky
        # watchlist BURNIE would have been re-scanned every cycle from the
        # moment it first entered the universe.
        # Format: addr -> {pair, last_seen_ts}. TTL 12h. Persisted to disk.
        self._sticky_watchlist: Dict[str, dict] = {}
        self._sticky_ttl_secs = 12 * 3600
        self._sticky_path = os.path.join(
            os.environ.get("DATA_DIR", "/data"), "sticky_watchlist.json"
        )
        self._load_sticky()
        # ── User watchlist (April-era specialization model) ─────────────
        # Curator-driven token tracking. Add addresses via
        # USER_WATCHLIST_ADDRESSES env var (comma-separated).
        # Watchlist tokens are force-fetched every cycle and bypass
        # discovery-noise filters (mcap_low, vol_h24 min, turnover,
        # loss_cooldown) so the bot can revisit the same handful of
        # "tokens of the moment" — same pattern April high-WR era used
        # organically. Keeps all "buying high" and "dying volume"
        # protections active (filter_topping, filter_chasing_bounce,
        # filter_blowoff_top, FRESHNESS GATE, vol_h1_decay).
        _user_watch_raw = os.environ.get("USER_WATCHLIST_ADDRESSES", "")
        self._user_watchlist_addrs: set = {
            a.strip().lower() for a in _user_watch_raw.split(",") if a.strip()
        }
        if self._user_watchlist_addrs:
            logger.info(
                f"[DipScanner] User watchlist loaded: "
                f"{len(self._user_watchlist_addrs)} addresses"
            )
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
            # User watchlist: bypass discovery-noise gates so we can revisit
            # the same handful of curated tokens every cycle.
            _user_watch = _addr_lower in self._user_watchlist_addrs
            if _user_watch:
                c["user_watchlist_eval"] = c.get("user_watchlist_eval", 0) + 1
            if _addr_lower in self.open_positions_ref or token_address in self.open_positions_ref:
                c["already_open"] += 1
                continue
            # Per-token loss cooldown — block rebuy for 30min after a losing
            # dip_buy close on the same token.  Same-token rebuy-after-loss
            # historically nets ~$0 (n=161 across history) but causes acute
            # bleed when a token enters a downtrend (e.g. mexicanunc 4-stop
            # cycle today).  30-min window saves ~$267 today and only ~$41
            # of MAGA's ladder-up wins lifetime.
            # USER_WATCHLIST bypass: the whole point of curated tokens is to
            # re-enter on the next dip after a loss; cooldown defeats this.
            if hasattr(self.trader, "is_dip_in_cooldown") and \
                    self.trader.is_dip_in_cooldown(token_address, 1800):
                c["loss_cooldown"] += 1
                if not _user_watch:
                    continue

            mcap = pair.get("marketCap") or 0
            # USER_WATCHLIST bypass: small-cap floor not applicable for
            # user-curated tokens (user chose them deliberately).
            if mcap < self.min_mcap:
                c["mcap_low"] += 1
                if not _user_watch:
                    continue
            if mcap > self.max_mcap:
                c["mcap_high"] += 1
                continue

            created_ms = pair.get("pairCreatedAt") or 0
            if created_ms <= 0 or (now_ms - created_ms) < self.min_age_ms:
                c["age"] += 1
                continue

            vol_h24 = (pair.get("volume") or {}).get("h24", 0) or 0
            # USER_WATCHLIST bypass: vol_h24 minimum for universe discovery,
            # not for curated tokens. Real dying-volume protection lives
            # in FRESHNESS GATE and vol_h1_decay below — those stay on.
            if vol_h24 < self.min_volume_h24:
                c["vol"] += 1
                if not _user_watch:
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
            # USER_WATCHLIST bypass: turnover floor is a universe-discovery
            # filter to skip over-liquid pools where trades don't move price.
            # User-curated tokens get evaluated regardless.
            if liq_usd > 0 and turnover < self.min_turnover_h24:
                c["low_turnover"] += 1
                if not self.baseline_mode and not _user_watch:
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

            # FRESHNESS GATE 2026-05-17: block tokens with dying volume.
            # When the bot's snapshot captures pump-era features but the
            # actual recent activity has collapsed, stale cached features
            # (bs ratios, breakthrough triggers) light up like a bullish
            # setup. By execution the token is a corpse. Reference: PAC
            # 2026-05-16 21:34 manual close at -$1.74 (feedback_dying_
            # volume_close.md); PAC 2026-05-17 02:45+ same pattern firing
            # 11 stacked breakthrough triggers on $66 5m vol / 2 txns.
            #
            # Predicate: vol_m5 < $200 AND total_5m_txns <= 4 (BOTH must
            # be low — single thin metric isn't sufficient).
            _txn_m5_obj = (pair.get("txns") or {}).get("m5") or {}
            _txn_m5_buys = _txn_m5_obj.get("buys", 0) or 0
            _txn_m5_sells = _txn_m5_obj.get("sells", 0) or 0
            _txn_m5_total = _txn_m5_buys + _txn_m5_sells
            if vol_m5 < 200.0 and _txn_m5_total <= 4:
                c["vol_m5_dead"] = c.get("vol_m5_dead", 0) + 1
                logger.info(
                    f"[DipScanner] FRESHNESS GATE blocked: "
                    f"{pair.get('baseToken', {}).get('symbol', '?')} "
                    f"vol_m5=${vol_m5:.0f}<$200 AND txns_m5={_txn_m5_total}<=4 "
                    f"(dying volume — refuses stale-cache breakthrough fires)"
                )
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

            # red_h24 gate — LOOSENED 2026-05-14 PM from <=0 to <-5.
            # Previously required token to be GREEN on 24h (pc_h24 > 0).
            # That requirement was pro-cyclical: in bear macros fewer tokens
            # are 24h-green, so the bot starves. Loosened to -5% to allow
            # tokens that are slightly red on 24h as real dip candidates.
            # Below -5% is genuine downtrend (filter separately).
            #
            # 2026-05-17 CARVE-OUT: broader rescue for deep-red tokens that
            # show real activity. Universe-recorder mining (n=2691, 24h):
            #   OOS rescue: pc_h24<-5 BUT vol_h6>=296k   n=387 68% WR5 +$695/d
            #   OOS rescue: pc_h24<-5 BUT buys_h1>=1331  n=274 74% WR5 +$589/d
            #   OOS rescue: pc_h24<-5 BUT pc_h1<=-16.89  n=274 70% WR5 +$476/d
            #   OOS rescue: pc_h24<-5 BUT vol_m5>=31280  n=110 83% WR5 +$271/d
            # All four legs are independently positive-EV. Any-of trigger
            # rescues ~400-600/day at 68-83% WR (vs 54.4% blanket OOS).
            #
            # Reference: VIRL 2026-05-17 ~03:00 UTC — pc_h24=-21.5%,
            # pc_h1=+4.7%, pc_m5=+4.2% (bot bought it 7x profitably earlier
            # today when 24h was green). Carve-out unlocks the V-bottom
            # rebuy. Watch `red_h24_rescued` counter — revert if cohort
            # underperforms over 50+ trades.
            if pc_h24 < -5:
                buys_h1_v = (pair.get("txns") or {}).get("h1", {}).get("buys", 0) or 0
                vol_h6_v = (pair.get("volume") or {}).get("h6", 0) or 0
                _rescue_vol_h6 = vol_h6_v >= 296_834
                _rescue_buys_h1 = buys_h1_v >= 1331
                _rescue_h1_dip = pc_h1 <= -16.89
                _rescue_vol_m5 = vol_m5 >= 31_280
                _red_h24_rescue = (
                    _rescue_vol_h6 or _rescue_buys_h1 or _rescue_h1_dip or _rescue_vol_m5
                )
                if not _red_h24_rescue:
                    c["red_h24"] += 1
                    if not self.baseline_mode:
                        continue
                else:
                    c["red_h24_rescued"] = c.get("red_h24_rescued", 0) + 1

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
            #
            # 2026-05-15 CARVE-OUT: rescue if bs_m5 >= 2.0 (5m order flow
            # shows real accumulation on the pullback — DIAMOND-class runner
            # signature where dips keep getting bought). Validated thinly on
            # lifetime data: top-ex zone × bs_m5>=2.0 had 50% WR / -$1.46 net
            # (n=6) vs 44% WR / -$3.28 net (n=9) without the carve-out.
            # Sample is small AND biased (these trades passed top_ex in
            # baseline-mode or edge cases) — monitor forward, revert if WR
            # craters.
            if 50.0 <= pc_h6 <= 200.0 and pc_h1 >= 5.0:
                # Compute bs_m5 inline (full ratio_m5 isn't computed until
                # line ~583, but we need it here for the carve-out).
                _txns_m5_te = (pair.get("txns") or {}).get("m5") or {}
                _b_te = int(_txns_m5_te.get("buys") or 0)
                _s_te = int(_txns_m5_te.get("sells") or 0)
                if _s_te > 0:
                    _bsm5_te = _b_te / _s_te
                elif _b_te > 0:
                    _bsm5_te = float("inf")
                else:
                    _bsm5_te = 0.0
                if _bsm5_te >= 2.0:
                    # Rescued — real demand still buying the pullback. Log
                    # so we can audit which carve-outs fire.
                    c["top_exhaustion_rescued"] = c.get("top_exhaustion_rescued", 0) + 1
                    logger.info(
                        f"[DipScanner] top_exhaustion RESCUED: {token_symbol} "
                        f"pc_h6={pc_h6:+.1f}% pc_h1={pc_h1:+.1f}% bs_m5={_bsm5_te:.2f}>=2.0"
                    )
                else:
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
            # 2026-05-17 PM — loosened from (pc_h1>=0 AND pc_m5>=0) to require
            # CLEAR green (>+1% on both) before rejecting. Old gate killed
            # near-flat tokens (e.g. +0.1%/+0.1%) which could be early-uptrend
            # starts. Volume-recovery context: rejects ~24/cycle pre-loosening,
            # expect ~5-10/cycle post-loosening (15-20 more candidates pass).
            if pc_h1 > 1.0 and pc_m5 > 1.0:
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
                    # 2026-05-17 PM — universe-recorder analogs for new V-bottom
                    # triggers. range_pct_last = (high-low)/low*100 on latest 1m.
                    # cum_5m_pct = (latest_close / cs[0].close - 1) * 100 across
                    # the 5-candle window (~5 min).
                    _range_pct_last = (
                        ((last.high - last.low) / last.low * 100)
                        if last.low > 0 else 0.0
                    )
                    _cum_5m_pct = (
                        ((last.close / cs[0].close - 1) * 100)
                        if cs[0].close > 0 else 0.0
                    )
                    # 2026-05-17 PM — vol_prev3_avg (avg of 3 candles before
                    # latest). Used by trigger_fresh_runner_factory.
                    _prev3 = cs[-4:-1] if len(cs) >= 4 else []
                    _vol_prev3_avg = (
                        sum(k.volume for k in _prev3) / len(_prev3)
                        if _prev3 else 0.0
                    )
                    # 2026-05-18 — vol_prev15_avg (avg of 15 candles before
                    # latest) computed from the full 1m series, not the cs
                    # 5-candle slice. Used to derive vol_accel for
                    # per-cohort entry-timing gate on trigger_fresh_runner_factory.
                    _full_1m_vol = _chart_data.candles_1m if _chart_data and _chart_data.candles_1m else []
                    _prev15_full = _full_1m_vol[-16:-1] if len(_full_1m_vol) >= 16 else []
                    _vol_prev15_avg = (
                        sum(k.volume for k in _prev15_full) / len(_prev15_full)
                        if _prev15_full else 0.0
                    )
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
                        # universe-recorder feature analogs (2026-05-17 PM)
                        "1m_range_pct_last": round(_range_pct_last, 3),
                        "1m_cum_5m_pct":     round(_cum_5m_pct, 3),
                        "1m_vol_prev3_avg":  round(_vol_prev3_avg, 3),
                        "1m_vol_prev15_avg": round(_vol_prev15_avg, 3),
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
                    # D1 — chart_trend_features (slopes, HH/LH, MA distances,
                    # slope acceleration). Forward-only collection: features
                    # appear in entry_meta. Cannot backfill lifetime trades.
                    # See feeds/chart_trend_features.py for hypothesis.
                    try:
                        from feeds.chart_trend_features import compute_chart_trend
                        _full_1m_t = (_chart_data.candles_1m if _chart_data and _chart_data.candles_1m else [])
                        _trend_feats = compute_chart_trend(_full_1m_t)
                        m1_features.update(_trend_feats)
                    except Exception as _e:
                        logger.debug(f"[DipScanner] chart_trend error for {token_symbol}: {_e}")
                    # Path 2 / D1 ext — micro chart pattern detection (named
                    # patterns on 1m bars): double top/bottom, head-shoulders,
                    # wedges, flags, triangles, engulfing, wick rejections.
                    # Emits chart_micro_pattern_score (-100 bearish → +100 bullish)
                    # plus individual detection flags. Forward-only, no
                    # filtering yet. See feeds/chart_micro_patterns.py.
                    try:
                        from feeds.chart_micro_patterns import compute_micro_patterns
                        _full_1m_m = (_chart_data.candles_1m if _chart_data and _chart_data.candles_1m else [])
                        _micro_feats = compute_micro_patterns(_full_1m_m)
                        m1_features.update(_micro_feats)
                    except Exception as _e:
                        logger.debug(f"[DipScanner] micro_patterns error for {token_symbol}: {_e}")
                    # 2026-05-17 PM — loosened: only reject if no green AND
                    # cum_3min is meaningfully negative (still dropping). Near-
                    # flat tokens (cum_3min in [-2, 0]) are stable bottom
                    # candidates — let them through to deeper analysis. Volume-
                    # recovery context: rejects ~13/cycle pre-loosening, expect
                    # ~5-8/cycle post-loosening.
                    if green_in_last3 == 0 and cum_3min_pct < -2.0:
                        c["no_1m_reversal"] += 1
                        logger.info(
                            f"[DipScanner] 1m gate: {token_symbol} — "
                            f"no green close in last 3 min AND cum_3min={cum_3min_pct:+.1f}%<-2 "
                            f"— skipping"
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
                # 300s cache (vs 60s default) — SOL fetch was missing
                # ~80% of trades due to per-token contention + 60s cache
                # expiring mid-cycle on the shared GT 25-req/min budget.
                # Regime use only needs minute-precision SOL within ~5min;
                # the 300s cache absorbs rate-limit pressure (2026-05-12).
                sol_5m = await self.gt_client.fetch_5m(_SOL_POOL, limit=48,
                                                      cache_ttl_override=300)
                sol_1m = await self.gt_client.fetch_1m(_SOL_POOL, limit=5,
                                                      cache_ttl_override=300)
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

            # ── Jito MEV tip floor (macro sniper-aggression proxy) ──────────
            # Cached 60s. Fail-open: empty dict on any error. Joined into
            # sol_features so it lands in entry_meta alongside SOL/BTC/meme.
            try:
                from feeds.jito_bundle_feed import get_default_feed as _get_jito_feed
                _jito_snap = await _get_jito_feed().snapshot()
                if isinstance(_jito_snap, dict):
                    for _k, _v in _jito_snap.items():
                        if _v is not None:
                            sol_features[_k] = _v
            except Exception as _e:
                logger.debug(f"[DipScanner] Jito snapshot err: {_e}")

            # ── Smart-money registry features ───────────────────────────────
            # AxiomSmartWalletTracker publishes every tracked-wallet buy to
            # the in-memory registry. Read recent activity for THIS token
            # and stamp into entry_meta. Three scalars exposed; all None
            # if no smart-wallet activity in lookback window.
            smart_money_features: dict = {}
            try:
                from feeds.smart_money_registry import get_default_registry
                smart_money_features = get_default_registry().smart_money_features(
                    token_address, lookback_s=300.0
                )
            except Exception as _e:
                logger.debug(f"[DipScanner] smart_money snapshot err: {_e}")

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
                    (buy_impact_pct, sell_impact_pct) or (None, None).

                    2026-05-10 fix: sell-side quote was failing 99% of the time
                    on the canonical post-CB cohort (only 3/1011 trades had
                    slip_sell_5000_pct populated). Root cause: at high USD sizes
                    the buy outAmount can exceed Jupiter's available exit
                    routes (esp. on small/illiquid pools), so the sell quote
                    returns no route. Fallback now retries the sell quote at
                    1/2 and 1/4 of buy outAmount when the full-size sell fails;
                    si is recorded with a normalized scaling so downstream
                    filters see comparable values across trades. Also raise
                    slippageBps to 1500 on retry — the high-impact trades we
                    care about discriminating against would never fit at 3%."""
                    sol_amount = usd / max(sol_price_est, 1.0)
                    lamports = max(int(sol_amount * 1e9), 1_000_000)
                    buy_q = await _quote(session, {
                        "inputMint": _SOL_MINT, "outputMint": token_address,
                        "amount": lamports, "slippageBps": 300,
                    })
                    if not buy_q or not buy_q.get("outAmount"):
                        return (None, None)
                    bi = float(buy_q.get("priceImpactPct") or 0) * 100
                    out_amt = int(buy_q["outAmount"])
                    si = None
                    # Try full size first, then 50%, then 25% — record si
                    # at whichever size succeeds.
                    for frac, slip_bps in ((1.0, 300), (0.5, 800), (0.25, 1500)):
                        amount_try = max(int(out_amt * frac), 1)
                        sell_q = await _quote(session, {
                            "inputMint": token_address, "outputMint": _SOL_MINT,
                            "amount": amount_try, "slippageBps": slip_bps,
                        })
                        if sell_q and sell_q.get("priceImpactPct") is not None:
                            si = float(sell_q.get("priceImpactPct")) * 100
                            break
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
            # Carve-out 2026-05-14: rescue if sells_per_min_recent < 20.
            # Mechanism: when overall sell pressure is calm, the "1m green
            # on dead vol" signal becomes spurious — there's no real
            # distribution event, so the bounce isn't necessarily fake.
            # Validation on recent 7d (May 2-9, n=5 fake_bounce blocks):
            # rescues Goblin (sells/min=7,+$3.72), Trollpface (12,+$1.40),
            # BELIEVE (0,+$1.49), CHUD (16,+$0.48) — all 4 blocked winners.
            # Keeps FOFAR (sells/min=26,-$2.14) blocked. Clean separator
            # with margin (max winner 16 vs loser 26).
            if _filter_fake_bounce_block_reasons:
                _fb_spm = None
                try:
                    from feeds.trade_velocity import analyze as _fb_tv_analyze
                    _fb_spm = _fb_tv_analyze(recent_trades or []).get(
                        "sells_per_min_recent"
                    )
                except Exception:
                    pass
                if (
                    isinstance(_fb_spm, (int, float))
                    and not isinstance(_fb_spm, bool)
                    and _fb_spm < 20
                ):
                    logger.info(
                        f"[DipScanner] filter_fake_bounce RESCUED: {token_symbol} "
                        f"sells_per_min_recent={_fb_spm:.1f}<20 (calm tape carve-out)"
                    )
                    _filter_fake_bounce_block_reasons = []
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
            # CARVE-OUT 2026-05-16 PM: rescue when vol_h24 <= $1.4M.
            # Mining: 71 round-trip-blocked events with vol24h_k<=1396 hit
            # 63% won_10pct (+24.9% avg peak) vs 52% baseline. Smaller-vol
            # tokens that pulled back have higher mean-reversion success.
            _round_trip_carve = False
            if (_filter_round_trip_block_reasons
                and vol_h24 is not None and float(vol_h24) <= 1_396_000):
                _round_trip_carve = True
            _filter_round_trip_verdict = (
                "BLOCK" if (_filter_round_trip_block_reasons and not _round_trip_carve)
                else "PASS"
            )
            c[f"filter_round_trip_{_filter_round_trip_verdict.lower()}"] = c.get(
                f"filter_round_trip_{_filter_round_trip_verdict.lower()}", 0
            ) + 1
            if _filter_round_trip_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_round_trip: {token_symbol} "
                    f"reasons={','.join(_filter_round_trip_block_reasons)}"
                )
                continue
            if _round_trip_carve and _filter_round_trip_block_reasons:
                logger.info(
                    f"[DipScanner] filter_round_trip RESCUED by vol_h24=${float(vol_h24)/1000:.0f}k<=1396k: "
                    f"{token_symbol}"
                )
                c["filter_round_trip_carve_vol"] = c.get("filter_round_trip_carve_vol", 0) + 1

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
            # DEMOTED to SHADOW 2026-05-14 evening — gather counterfactual
            # data (no BLOCK→executed-trade samples exist; can't audit while
            # enforced). Re-evaluate after 24h of forward data.
            if _filter_weak_bounce_v2_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_weak_bounce_v2 SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_weak_bounce_v2_block_reasons)}"
                )
                # ENFORCEMENT REMOVED 2026-05-14 PM — shadow only.

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
            # Fail-open when pct_in_5m_range is undefined (no pair_addr or <4 5m candles).
            # GT 429s during high load can leave the variable unset; pre-existing latent
            # bug surfaced 2026-05-15 via WORLDCUP rate-limit storm.
            if "pct_in_5m_range" in dir() and pct_in_5m_range < 0.5:
                _filter_turn_block_reasons.append(
                    f"pct_in_5m_range={pct_in_5m_range:.3f}<0.5 (catching knife)"
                )
            _filter_turn_verdict = "BLOCK" if _filter_turn_block_reasons else "PASS"
            c[f"filter_turn_{_filter_turn_verdict.lower()}"] = c.get(
                f"filter_turn_{_filter_turn_verdict.lower()}", 0
            ) + 1
            # RE-PROMOTED to ENFORCED 2026-05-14 AM.
            # CARVE-OUT 2026-05-14 PM: rescue big-buyer entries
            # (liq_velocity_h1_usd_per_txn >= 115). On lifetime n=34, this
            # rescues 5 blocked winners (RAGEGUY, BUFO, COPPERINU, HANTA, RKC)
            # totaling +$2.68 with ZERO losers rescued.
            #
            # CARVE-OUT 2026-05-14 EVENING: also rescue high-conviction chart
            # signals (chart_score >= 56). On lifetime BLOCK trades (n=18),
            # max loser chart_score = 55.9 (RKC-loss); MASCOTS winner
            # chart_score=58.7. Adds 1 more winner rescue (+$0.41), 0 losers.
            # chart_score is computed below in the chart_reader block, so the
            # block decision is DEFERRED until after that runs.
            #
            # liq_velocity_h1 = vol_h1 / (buys_h1 + sells_h1). Computed inline
            # because volume_velocity_features dict is built later in the loop.
            _big_buyer_carve_out = False
            _lv_h1_inline = None
            try:
                _txn_h1_b = int((txns_h1 or {}).get("buys") or 0)
                _txn_h1_s = int((txns_h1 or {}).get("sells") or 0)
                _txn_h1_total = _txn_h1_b + _txn_h1_s
                if _txn_h1_total > 0 and vol_h1:
                    _lv_h1_inline = float(vol_h1) / _txn_h1_total
                    if _lv_h1_inline >= 115:
                        _big_buyer_carve_out = True
            except Exception:
                pass
            # NOTE: filter_turn block decision is DEFERRED to after chart_ctx
            # is computed (see "DEFERRED FILTER_TURN CHECK" below). This lets
            # the carve-out also consult chart_score.

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

            # Chart CNN inference — SHADOW 2026-05-15. Plugs into _chart_data
            # (already fetched above). Returns None if weights missing or render
            # failure; all degradation is silent. Output goes into entry_meta_dict.
            _cnn_pattern = None
            _cnn_pattern_conf = None
            _cnn_outcome_prob = None
            try:
                from core.chart_cnn_inference import get_inference
                _cnn_inf = get_inference()
                if not _cnn_inf.disabled and _chart_data:
                    _cnn_result = _cnn_inf.predict(
                        token_address=token_address,
                        candles_1m=_chart_data.candles_1m or [],
                        candles_5m=_chart_data.candles_5m or [],
                        candles_15m=_chart_data.candles_15m or [],
                    )
                    if _cnn_result:
                        _cnn_pattern = _cnn_result.get("pattern")
                        _cnn_pattern_conf = _cnn_result.get("pattern_conf")
                        _cnn_outcome_prob = _cnn_result.get("outcome_prob")
            except Exception as _e:
                logger.debug(f"[DipScanner] CNN inference err: {_e}")

            # Chart cluster inference — ENFORCED 2026-05-15.
            # Rug filter: blocks entries where chart shape matches Cluster
            # 19 (autoencoder + k-means discovered, 67% rug rate, -18.5%
            # avg P&L, 0% historical win rate on n=6). See
            # scripts/rug_predictor_analysis.py.
            _cnn_cluster_id = None
            _cluster_19_rug_block = False
            try:
                from core.chart_cluster_inference import get_cluster_inference
                _cluster_inf = get_cluster_inference()
                if not _cluster_inf.disabled and _chart_data:
                    _cnn_cluster_id = _cluster_inf.classify(
                        token_address=token_address,
                        candles_1m=_chart_data.candles_1m or [],
                        candles_5m=_chart_data.candles_5m or [],
                        candles_15m=_chart_data.candles_15m or [],
                    )
                    if _cluster_inf.is_rug_cluster(_cnn_cluster_id):
                        _cluster_19_rug_block = True
            except Exception as _e:
                logger.debug(f"[DipScanner] cluster classify err: {_e}")

            if _cluster_19_rug_block:
                logger.info(
                    f"[DipScanner] BLOCKED by filter_cluster_19_rug: "
                    f"{token_symbol} cluster_id={_cnn_cluster_id} "
                    f"(historical: 67% rug rate, -18.5% avg, 0% WR n=6)"
                )
                c["filter_cluster_19_rug_block"] = c.get("filter_cluster_19_rug_block", 0) + 1
                continue

            # Forward dataset collector — dumps image + context for every
            # evaluated candidate. Outcome label gets stamped later by the
            # trader on close. SHADOW only — pure data collection.
            try:
                from feeds.forward_dataset_collector import get_collector
                from datetime import datetime, timezone
                if _chart_data:
                    get_collector().dump_snapshot(
                        token_address=token_address,
                        ts_iso=datetime.now(timezone.utc).isoformat(),
                        candles_1m=_chart_data.candles_1m or [],
                        candles_5m=_chart_data.candles_5m or [],
                        candles_15m=_chart_data.candles_15m or [],
                        context={
                            "hour_ct": _flt_h if "_flt_h" in dir() else None,
                            "mcap_usd": mcap if "mcap" in dir() else None,
                            "token_symbol": token_symbol,
                        },
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] forward_collector err: {_e}")

            # ─── UptrendScanner SHADOW eval (Phase 1, 2026-05-14 evening) ──
            # Green-tape companion strategy. Evaluates the SAME token here
            # but with different gate/trigger logic targeting confirmed
            # uptrend regimes (mtf=bull AND 5m_state=uptrend) — the exact
            # regime filter_chasing_top rejects below. SHADOW only: logs
            # WOULD-FIRE / WOULD-BLOCK to {DATA_DIR}/uptrend_shadow.jsonl
            # without affecting any dip_scanner decision. Fail-open on any
            # exception — must not break the live dip_buy pipeline.
            try:
                from feeds.uptrend_scanner import get_instance as _ut_get
                _ut_get().evaluate(
                    token_symbol=token_symbol,
                    token_address=token_address,
                    chart_ctx_dict=_chart_ctx_dict,
                    m1_features=m1_features,
                    peak_h24_6h_pct=float(peak_h24_6h) if isinstance(peak_h24_6h, (int, float)) else None,
                    lifecycle_h24_ratio=None,  # not yet computed at this point
                )
            except Exception as _ut_e:
                logger.debug(f"[UptrendScanner] shadow eval error: {_ut_e}")

            # ── trigger_post_capit_breakout (positive V-bottom reversal) ──
            # ENFORCED 2026-05-15 with carve-outs on filter_turn /
            # filter_sweep_too_recent / filter_chasing_top. Filter
            # filter_mtf_strong_downtrend (mtf<=-2) is INTENTIONALLY kept
            # as the hard safety floor — blocks the worst bear regimes.
            #
            # Mechanism: catches the V-bottom rebound that fires when
            # multi-TF was bearish recently AND the latest 1m bar prints
            # a strong green confirmation candle with volume. Positive-side
            # companion to filter_falling_knife (opposite shape: knife
            # blocks mtf<=-1 AND 1m red; this fires on 1m green after
            # bearish context).
            #
            # Predicate:
            #   1m_last_close_pct >= 2.0  (1m green confirmation)
            #   AND 1m_volume_spike >= 2.0 (vol kicker on the green bar)
            #   AND pc_h1 < 0 (1h is red — was dipping)
            #
            # Mining (signal_events 2026-05-14/15, ~26h, n=25 forward-
            # traceable matches): predicate matches ~24/d, ALL currently
            # blocked by sweep_too_recent / turn / chasing_top / mtf_strong_dn.
            # Forward outcome (pc_h24 delta proxy in 15min window): 20%
            # hit +5pp, 24% hit -7pp, fat-tail wins (CBRS +434pp seen).
            # The TP1=5/stop=-7 simple model is -0.44pp/trade negative,
            # but trail-from-peak on the fat tail makes it positive in
            # expectation. Surfaces will validate the actual fill outcome.
            #
            # Carved-out filters when this trigger matches:
            #   - filter_turn (knife-catch)
            #   - filter_sweep_too_recent (recent low)
            #   - filter_chasing_top (5m uptrend chase)
            # Kept active:
            #   - filter_mtf_strong_downtrend (mtf <= -2, hard floor)
            #   - all other filters (defensive layer preserved)
            _pcb_lcp_v = m1_features.get("1m_last_close_pct") if isinstance(m1_features, dict) else None
            _pcb_vs_v = m1_features.get("1m_volume_spike") if isinstance(m1_features, dict) else None
            _trigger_post_capit_breakout_match = False
            _trigger_post_capit_breakout_reasons: list = []
            try:
                if (
                    _pcb_lcp_v is not None and float(_pcb_lcp_v) >= 2.0
                    and _pcb_vs_v is not None and float(_pcb_vs_v) >= 2.0
                    and pc_h1 is not None and float(pc_h1) < 0
                ):
                    _trigger_post_capit_breakout_match = True
                    _trigger_post_capit_breakout_reasons.append(
                        f"1m_last_close={float(_pcb_lcp_v):+.2f}%>=2 AND "
                        f"1m_vol_spike={float(_pcb_vs_v):.2f}>=2 AND "
                        f"pc_h1={float(pc_h1):+.2f}%<0 (post-capit V-bottom confirmation)"
                    )
            except Exception as _pcb_e:
                logger.debug(f"[DipScanner] trigger_post_capit_breakout err: {_pcb_e}")

            # ─── DEFERRED FILTER_TURN CHECK ────────────────────────────────
            # filter_turn verdict + big_buyer carve-out were computed earlier.
            # Now that chart_score is available, also check the chart_score
            # carve-out (>= 56) before blocking. Plus the post-capit-breakout
            # carve-out (V-bottom reversal pattern) added 2026-05-15.
            if _filter_turn_verdict == "BLOCK":
                _chart_score_for_carve = (_chart_ctx_dict or {}).get("chart_score")
                _chart_carve_out = (
                    isinstance(_chart_score_for_carve, (int, float))
                    and _chart_score_for_carve >= 56
                )
                _pcb_carve_out = _trigger_post_capit_breakout_match
                # CARVE-OUT 2026-05-16 PM: rescue when bs_h6 <= 1.20.
                # Mining: turn-blocked + bs_h6<=1.20 → n=295, 64% won_10pct,
                # +20.6% avg peak vs 49% blocks-baseline. Low bs_h6 means
                # the 6h ratio is at-or-below baseline (not over-extended);
                # turn filter's "catching knife" thesis doesn't apply when
                # the 6h structure is balanced.
                _bs_h6_carve_out = (
                    ratio_h6 is not None and float(ratio_h6) <= 1.20
                )
                if (not _big_buyer_carve_out and not _chart_carve_out
                        and not _pcb_carve_out and not _bs_h6_carve_out):
                    logger.info(
                        f"[DipScanner] BLOCKED by filter_turn: {token_symbol} "
                        f"reasons={','.join(_filter_turn_block_reasons)}"
                    )
                    try:
                        from feeds.filter_shadow_recorder import get_recorder as _gfsr
                        _gfsr().record(
                            token_address=token_address, token_symbol=token_symbol,
                            pair=pair, filter_name="filter_turn", verdict="BLOCK",
                            block_reasons=",".join(_filter_turn_block_reasons),
                        )
                    except Exception:
                        pass
                    continue
                elif _big_buyer_carve_out:
                    logger.info(
                        f"[DipScanner] filter_turn rescued by big_buyer carve-out: "
                        f"{token_symbol} liq_velocity_h1=${_lv_h1_inline:.0f}/txn>=115"
                    )
                elif _chart_carve_out:
                    logger.info(
                        f"[DipScanner] filter_turn rescued by chart_score carve-out: "
                        f"{token_symbol} chart_score={_chart_score_for_carve:.1f}>=56"
                    )
                elif _pcb_carve_out:
                    logger.info(
                        f"[DipScanner] filter_turn rescued by post_capit_breakout carve-out: "
                        f"{token_symbol} {';'.join(_trigger_post_capit_breakout_reasons)}"
                    )
                elif _bs_h6_carve_out:
                    logger.info(
                        f"[DipScanner] filter_turn rescued by bs_h6 carve-out: "
                        f"{token_symbol} bs_h6={float(ratio_h6):.2f}<=1.20 "
                        f"(64% won_10pct on n=295 in 4d mining)"
                    )
                    c["filter_turn_carve_bs_h6"] = c.get("filter_turn_carve_bs_h6", 0) + 1

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
            # Record decision (BLOCK or PASS) for retrospective audit.
            try:
                from feeds.filter_shadow_recorder import get_recorder as _gfsr
                _gfsr().record(
                    token_address=token_address,
                    token_symbol=token_symbol,
                    pair=pair,
                    filter_name="filter_vp_poc",
                    verdict=_filter_vp_poc_verdict,
                    block_reasons=",".join(_filter_vp_poc_block_reasons),
                )
            except Exception:
                pass
            # 2026-05-18 — RE-ENFORCED. Lifetime audit (n=128 closed):
            # BLOCK n=19, avg -0.75%, save +14pp. Mira buy 1 had
            # vp_poc_distance=+45.9% AND 1m_vol_spike=0.12, blocked
            # by this. Pure chase pattern: extreme above POC on dead
            # volume. POP-class winner regret is small vs Mira-class
            # loss avoidance.
            if _filter_vp_poc_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_vp_poc: {token_symbol} "
                    f"reasons={','.join(_filter_vp_poc_block_reasons)}"
                )
                c["filter_vp_poc_block"] = c.get("filter_vp_poc_block", 0) + 1
                if not _user_watch:
                    continue
                logger.info(f"[DipScanner] WATCHLIST BYPASS filter_vp_poc: {token_symbol}")

            # Filter sweep-too-recent — ENFORCED 2026-05-13.
            # Catches the dominant 2026-05-12 overnight loser shape: bot bought
            # while the 5m chart's sweep-low was still actively unfolding
            # (chart_sweep_5m_low_candles_ago <= 2 = the sweep happened in the
            # last ≤10min and price hasn't consolidated yet). Knife-catching
            # mid-dump.
            #
            # Methodology: angle-7 single-feature mining across the 23-trade
            # post-deploy sample found 7/7 losers had this feature <= 2 while
            # 0/8 winners did. Then validated on 7d held-out (364 closed) —
            # every trigger family (clean_break, high_regime, informed_cluster,
            # patient_bottom, grad_window_dip) showed BLOCK WR < ALLOW WR by
            # +14% to +50%. Not trigger-specific; global anti-knife-catch.
            #
            # Threshold sweep on 7d: <=1 NET +$46, <=2 NET +$73 (best), <=3
            # NET +$67, <=4 NET +$69. Choose <=2 — optimal save/win-killed.
            #
            # 7d held-out: blocks 83 of 364, BLOCK WR 24% vs ALLOW WR 40%
            # (Delta +16%). Saves ~$94 losses, kills ~$21 in winners (20
            # killed, largest $2.14, avg $1.03 — no big winners sacrificed).
            # NET ~$73/wk. Stable 7/8 days.
            #
            # In-sample (2026-05-12 overnight, n=23): blocks 7 of 15 losers
            # (0% WR on blocked), allowed set goes from 35% WR -> 50% WR.
            #
            # Coverage: 36% of trades have this feature populated (the rest
            # fail-open). The no-feature cohort is the healthier 64% (40% WR
            # vs 31% WR for the with-feature cohort) — filter correctly
            # targets the riskier subset.
            _swp_ago = _chart_ctx_dict.get("chart_sweep_5m_low_candles_ago")
            _filter_sweep_too_recent_block_reasons: list = []
            if _swp_ago is not None and _swp_ago <= 2:
                _filter_sweep_too_recent_block_reasons.append(
                    f"chart_sweep_5m_low_candles_ago={_swp_ago:.0f}<=2 "
                    f"(sweep still unfolding — knife-catch)"
                )
            _filter_sweep_too_recent_verdict = (
                "BLOCK" if _filter_sweep_too_recent_block_reasons else "PASS"
            )
            c[f"filter_sweep_too_recent_{_filter_sweep_too_recent_verdict.lower()}"] = c.get(
                f"filter_sweep_too_recent_{_filter_sweep_too_recent_verdict.lower()}", 0
            ) + 1
            if _filter_sweep_too_recent_verdict == "BLOCK":
                if _trigger_post_capit_breakout_match:
                    logger.info(
                        f"[DipScanner] filter_sweep_too_recent rescued by "
                        f"post_capit_breakout carve-out: {token_symbol} "
                        f"{';'.join(_trigger_post_capit_breakout_reasons)}"
                    )
                else:
                    # SHADOW 2026-05-15: filter_sweep_too_recent moved
                    # ENFORCED → SHADOW. Reason: audit showed Odyssey
                    # (+44% end gain WIN) blocked by sweep_5m_low_ago=1.
                    # The filter catches "still falling" but the same
                    # signature is present in v-bottom-reversal cases
                    # right before the recovery candle prints. Recorder
                    # captures forward outcomes for both BLOCK and PASS;
                    # promote back if BLOCK cohort cleanly underperforms.
                    logger.info(
                        f"[DipScanner] filter_sweep_too_recent SHADOW would-block: {token_symbol} "
                        f"reasons={','.join(_filter_sweep_too_recent_block_reasons)}"
                    )
                    try:
                        from feeds.filter_shadow_recorder import get_recorder as _gfsr
                        _gfsr().record(
                            token_address=token_address, token_symbol=token_symbol,
                            pair=pair, filter_name="filter_sweep_too_recent", verdict="BLOCK",
                            block_reasons=",".join(_filter_sweep_too_recent_block_reasons),
                        )
                    except Exception:
                        pass

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
                    compute_bottom_signature_v1,
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
                # 7. Bottom signature v1 — SHADOW 2026-05-13.
                # Universal-coverage bottom-detection features from 1m+5m.
                _cs1_full = (_chart_data.candles_1m if _chart_data and _chart_data.candles_1m else [])
                _tier2_features.update(
                    compute_bottom_signature_v1(_cs1_full, _cs5_full)
                )
            except Exception as _e:
                logger.debug(f"[DipScanner] tier2 features error: {_e}")

            # 7. Cross-token regime breadth (computed once per scan cycle above)
            _tier2_features["regime_dip_breadth_pct"] = _regime_dip_breadth_pct
            _tier2_features["regime_h1_neg_pct"] = _regime_h1_neg_pct
            _tier2_features["regime_n_tokens_scanned"] = _regime_n

            # Filter rsi_overbought — ENFORCED 2026-05-11.
            # Mined from 684 modern trades (with lifecycle_age tracking):
            #   rsi_5m < 50: WR 75% (n=253, 37% fire rate) — CV 75%, $+0.12/tr
            #   rsi_5m 50-60: WR 56% ← sharp cliff
            #   rsi_5m 60-70: WR 65% (small sample noise)
            #   rsi_5m 70+: WR 44%
            # Modern baseline is 58% WR / -$0.22 per trade — RSI gate lifts
            # to 75% WR / +$0.12. 5-fold CV (token-disjoint) confirmed.
            # Mechanism: RSI<50 means 5m chart's downside momentum hasn't
            # reset — we're buying into ongoing weakness (correct for dip).
            # RSI>=50 means momentum already neutral/up — likely buying a
            # bounce that's already played out.
            #
            # Fail-open if rsi_5m missing (tier2 fetch failed) — feature
            # has ~80% coverage; don't penalize on missing data.
            #
            # RETUNED 2026-05-13 PM: threshold 50 -> 55. Original 50 was
            # blocking too many neutral-zone candidates (RSI 50-55 is
            # "balanced", not actually overbought). filter_rsi_overbought
            # was the #1 pre-trigger blocker (6 blocks per 1500 log lines)
            # — cutting trigger-eligible volume too hard. The original
            # mining showed 50-60 bucket had 56% WR which is BELOW baseline
            # but not catastrophic; 55-60 may be acceptable. Watching
            # forward to validate.
            _rsi5 = _tier2_features.get("rsi_5m")
            _filter_rsi_overbought_block_reasons: list = []
            if _rsi5 is not None and _rsi5 >= 55:
                _filter_rsi_overbought_block_reasons.append(
                    f"rsi_5m={_rsi5:.1f}>=55 (5m momentum reset, not oversold)"
                )
            _filter_rsi_overbought_verdict = (
                "BLOCK" if _filter_rsi_overbought_block_reasons else "PASS"
            )
            c[f"filter_rsi_overbought_{_filter_rsi_overbought_verdict.lower()}"] = c.get(
                f"filter_rsi_overbought_{_filter_rsi_overbought_verdict.lower()}", 0
            ) + 1
            # DEMOTED to SHADOW 2026-05-14 evening — gather counterfactual
            # data (no BLOCK→executed-trade samples exist; can't audit while
            # enforced). Re-evaluate after 24h of forward data.
            if _filter_rsi_overbought_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] filter_rsi_overbought SHADOW would-block: {token_symbol} "
                    f"reasons={','.join(_filter_rsi_overbought_block_reasons)}"
                )
                # ENFORCEMENT REMOVED 2026-05-14 PM — shadow only.

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

            # ── Breakthrough-trigger EARLY preview (2026-05-16 PM) ─────────
            # The 6 on-chain compound triggers shipped 2026-05-15 had
            # 72-100% WR on lifetime data (reference_onchain_compound_
            # breakthrough). 2 of them (strong_orderflow, sustained_
            # accumulation) depend only on features available by this
            # point: ratio_m5/h1/h6, chart_mtf_score (via _chart_ctx_dict),
            # net_flow_60s_usd (via _tier3_features).
            #
            # The 4 remaining triggers need 1s features or
            # 1s_close_pos_60s computed later — those are covered by the
            # LATE breakthrough flag at the end of trigger eval.
            #
            # This EARLY flag lets filters that fire BEFORE the full
            # trigger-eval block at line ~6700 carve-out for high-WR
            # candidates. Predicates mirror the actual trigger eval
            # exactly — wash-guard (mean_buy_size_usd >= $10) is
            # applied at late eval, so EARLY flag is OPTIMISTIC and
            # the late guard may revoke it before entry.
            _breakthrough_early_match = False
            try:
                _bt_nf60 = _tier3_features.get("net_flow_60s_usd") if isinstance(_tier3_features, dict) else None
                _bt_mtf = (_chart_ctx_dict or {}).get("chart_mtf_score") if isinstance(_chart_ctx_dict, dict) else None
                _bt_ratio_m5 = ratio_m5 if ratio_m5 != float("inf") else None
                _bt_ratio_h1 = ratio_h1 if ratio_h1 != float("inf") else None
                _bt_ratio_h6 = ratio_h6 if ratio_h6 != float("inf") else None

                _bt_strong_orderflow = (
                    _bt_nf60 is not None and float(_bt_nf60) > 0
                    and _bt_mtf is not None and float(_bt_mtf) >= 1.0
                    and _bt_ratio_m5 is not None and _bt_ratio_m5 >= 1.5
                )
                _bt_sustained_accum = (
                    _bt_nf60 is not None and float(_bt_nf60) > 0
                    and _bt_mtf is not None and float(_bt_mtf) >= 0
                    and _bt_ratio_h1 is not None and _bt_ratio_h1 >= 1.5
                    and _bt_ratio_h6 is not None and _bt_ratio_h6 >= 1.2
                )
                _breakthrough_early_match = bool(
                    _bt_strong_orderflow or _bt_sustained_accum
                )
            except Exception as _bt_e:
                logger.debug(f"[DipScanner] breakthrough-early preview err: {_bt_e}")
                _breakthrough_early_match = False

            # ── high_activity_fast_path — 2026-05-17 ────────────────────
            # Bypass downstream trader filters (filter_combo_v2,
            # filter_chart_bear, filter_top10_holder_band) when token
            # matches one of 3 high-activity in-scope cohorts mined from
            # universe_recorder (n=2691, 24h):
            #   #1: vol_h6 >= 296k                          (n=959 in-scope, 75% WR5)
            #   #2: buys_h1 >= 1909 AND sells_h1 >= 1094    (n=425 in-scope, 81% WR5)
            #   #3: pc_h6 >= 82.68 AND buys_h1 >= 1909      (n=387 in-scope, 80% WR5)
            # Combined coverage: ~1,000-1,500 events/day at 75-81% WR.
            #
            # Why bypass: these cohorts have >baseline WR even when
            # trader-side filters would block them. Trader filters were
            # tuned on a smaller historic cohort and over-rejected
            # high-activity tokens — see PAC 2026-05-16 incident where
            # filter_combo_v2 blocked 11 stacked breakthrough triggers.
            #
            # Risk: #1 is the lowest-WR leg (75%) and broadest cohort.
            # Watch `high_activity_fast_path_used` counter. If forward
            # cohort WR drops below 60%, revert leg #1.
            _high_activity_fast_path = False
            try:
                _ha_txns_h1 = (pair.get("txns") or {}).get("h1", {}) or {}
                _ha_buys_h1 = float(_ha_txns_h1.get("buys", 0) or 0)
                _ha_sells_h1 = float(_ha_txns_h1.get("sells", 0) or 0)
                _ha_vol_h6 = float((pair.get("volume") or {}).get("h6", 0) or 0)
                _ha_high_vol = _ha_vol_h6 >= 296_834
                _ha_active_balanced = (_ha_buys_h1 >= 1909 and _ha_sells_h1 >= 1094)
                _ha_momentum_active = (pc_h6 >= 82.68 and _ha_buys_h1 >= 1909)
                _ha_cohort_match = (
                    _ha_high_vol or _ha_active_balanced or _ha_momentum_active
                )
                # 2026-05-17 PM — freshness precondition. fluff (8Hf1E…)
                # bought at 17:54 UTC matched _ha_active_balanced (high
                # historical txns) but was in real-time crash:
                # 1m_vol_spike=0.354, pc_m5=-29.9%, pc_h1=-37%.
                # Require live activity (1m_vol_spike >= 0.40 AND
                # 1m_cum_3min_pct >= -3.0) before granting the bypass.
                # m1_features may be missing if 1m fetch failed — fail-closed.
                _ha_m1 = locals().get('m1_features', {}) or {}
                _ha_vspike = _ha_m1.get('1m_volume_spike')
                _ha_cum3 = _ha_m1.get('1m_cum_3min_pct')
                _ha_fresh_ok = (
                    _ha_vspike is not None and float(_ha_vspike) >= 0.40
                    and _ha_cum3 is not None and float(_ha_cum3) >= -3.0
                )
                _high_activity_fast_path = bool(_ha_cohort_match and _ha_fresh_ok)
            except Exception as _ha_e:
                logger.debug(f"[DipScanner] high_activity_fast_path err: {_ha_e}")
                _high_activity_fast_path = False

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

            # ── filter_dev_rugged — ENFORCED 2026-05-10 ───────────────────────
            # Hard creator-rug gate: block any entry when dev_pct_remaining < 2.0
            # across ALL trigger paths. Existing Gate A in clean_break suppresses
            # at <1.0 (clean_break-only); this generalizes the gate at a slightly
            # looser threshold and applies to every entry path.
            #
            # Lifetime validation (n=1011 paired closed trades):
            #   dev_pct < 1.0  -> 4 fires, 25.0% WR, sc 7.22, net +$9.77
            #   dev_pct < 2.0  -> 7 fires, 28.6% WR, sc 5.38, net +$13.69 ← chosen
            #   dev_pct < 3.0  -> 14 fires, 50.0% WR, sc 2.39, net +$12.47
            #   dev_pct < 5.0  -> 74 fires, 52.7% WR, sc 1.44, net +$27.97 (diluted)
            # Held-out (n=203, last 4 days, last 20% of lifetime):
            #   <2.0 -> 7 fires, sc 5.38, +$13.69 (matches lifetime — clean signal)
            # Orthogonal to current shipped stack + Gate E (n=850):
            #   <2.0 -> 5 fires, 40% WR, sc 2.93, +$6.03 — still precise post-stack
            #
            # Mechanism: when the creator wallet has dumped >98% of its supply
            # before entry, there's no holder-aligned price floor — the team has
            # already captured value and exited. Stops follow soon. The 2.0
            # threshold is a sweet spot: <1.0 is too narrow (only catches the
            # most extreme rugs), <3.0+ starts cutting near-equal winners.
            #
            # Fail-open if dev_pct_remaining missing (Tier-1 not populated yet).
            if _dev_pct is not None and _dev_pct < 2.0:
                logger.info(
                    f"[DipScanner] BLOCKED by filter_dev_rugged: {token_symbol} "
                    f"dev_pct_remaining={_dev_pct:.2f}%<2.0 (creator rugged ≥98%)"
                )
                c["filter_dev_rugged_block"] = c.get("filter_dev_rugged_block", 0) + 1
                continue
            c["filter_dev_rugged_pass"] = c.get("filter_dev_rugged_pass", 0) + 1

            # ── filter_chasing_top — ENFORCED 2026-05-10 ──────────────────────
            # Block entries where the 5m chart is in active uptrend AND every
            # higher timeframe is also bullish. This is a "chasing the top"
            # signature — bot fires a 1m green candle while the broader trend
            # is still rising = chase, not dip.
            #
            # Lifetime (n=1011): 65 fires, 35.4% WR, save:cut 2.51, +$56.13
            # Held-out (n=203): 16 fires, 18.8% WR, save:cut 7.56, +$30.35
            # Orth held-out (n=123, after 11-filter stack): 12 fires, 25.0% WR,
            # save:cut 4.77, +$17.44 — 9L blocked, only 3W cut.
            #
            # Mechanism: dip-buy edge comes from buying retracements within
            # uptrends (downtrend on 5m + bullish higher TFs = real dip), or
            # buying breakouts of downtrends. Both timeframes pointing up =
            # entry is too late on the move.
            #
            # Fail-open if chart_ctx unavailable (dict missing / read errored).
            try:
                _ct_5m_state = _chart_ctx_dict.get("chart_structure_5m_state")
                _ct_mtf = _chart_ctx_dict.get("chart_mtf_alignment")
                if (_ct_5m_state == "uptrend"
                        and _ct_mtf in ("bull", "strong_bull")):
                    # CARVE-OUT 1: post_capit_breakout (V-bottom reversal).
                    # CARVE-OUT 2 (2026-05-15): strong_orderflow signature —
                    # if bs_m5>=1.5 AND net_flow_60s_usd>0 AND mtf_score>=1,
                    # the candidate matches the 100% lifetime WR trigger. Let
                    # it through filter_chasing_top so the trigger can fire.
                    _so_nf60 = _tier3_features.get("net_flow_60s_usd") if isinstance(_tier3_features, dict) else None
                    _so_mtf = (_chart_ctx_dict or {}).get("chart_mtf_score") if isinstance(_chart_ctx_dict, dict) else None
                    _so_rescue = (
                        _so_nf60 is not None and float(_so_nf60) > 0
                        and _so_mtf is not None and float(_so_mtf) >= 1.0
                        and ratio_m5 != float("inf") and ratio_m5 >= 1.5
                    )
                    if _trigger_post_capit_breakout_match:
                        logger.info(
                            f"[DipScanner] filter_chasing_top rescued by "
                            f"post_capit_breakout carve-out: {token_symbol} "
                            f"{';'.join(_trigger_post_capit_breakout_reasons)}"
                        )
                    elif _so_rescue:
                        logger.info(
                            f"[DipScanner] filter_chasing_top rescued by "
                            f"strong_orderflow carve-out: {token_symbol} "
                            f"bs_m5={ratio_m5:.2f}, mtf={float(_so_mtf):.1f}, "
                            f"net_flow_60s=${float(_so_nf60):+.0f}"
                        )
                        c["filter_chasing_top_rescued_orderflow"] = c.get(
                            "filter_chasing_top_rescued_orderflow", 0
                        ) + 1
                    else:
                        # DOWNGRADED TO SHADOW 2026-05-16 PM. 4d audit showed
                        # this filter blocked 97 events with 55% won_10pct
                        # (ABOVE 46% baseline) — actively harmful. The
                        # "chasing the top" thesis doesn't hold up: many of
                        # these tokens went on to peak +20%+. Logging in
                        # shadow recorder for forward attribution but NOT
                        # blocking. Re-promote only with strong evidence.
                        logger.info(
                            f"[DipScanner] SHADOW filter_chasing_top would-block: {token_symbol} "
                            f"5m_state=uptrend AND mtf={_ct_mtf} (DOWNGRADED 05-16, see audit)"
                        )
                        c["filter_chasing_top_shadow_block"] = c.get("filter_chasing_top_shadow_block", 0) + 1
                        try:
                            from feeds.filter_shadow_recorder import get_recorder as _gfsr
                            _gfsr().record(
                                token_address=token_address, token_symbol=token_symbol,
                                pair=pair, filter_name="filter_chasing_top", verdict="SHADOW_BLOCK",
                                block_reasons=f"5m_state=uptrend AND mtf={_ct_mtf}",
                            )
                        except Exception:
                            pass
                        # NOTE: removed `continue` — now passes through.
            except (NameError, AttributeError):
                pass  # chart_ctx not built — fail-open
            c["filter_chasing_top_pass"] = c.get("filter_chasing_top_pass", 0) + 1

            # ── filter_meteora_dex — ENFORCED 2026-05-10 ──────────────────────
            # Block entries on Meteora pools. DLMM dynamics differ from
            # Raydium / PumpSwap — slippage curve and LP behavior produce
            # systematically lower follow-through on dip-buys.
            #
            # Lifetime (n=1011): 50 fires, 38.0% WR, save:cut 2.06, +$35.73
            # Held-out (n=203): 17 fires, 29.4% WR, save:cut 3.99, +$22.36
            # Orth held-out (n=123): 11 fires, 27.3% WR, save:cut 3.97, +$12.91
            #
            # Mechanism: Meteora's dynamic-liquidity AMM concentrates liquidity
            # near current price; on dips with no active rebalance, exit
            # liquidity is asymmetric vs entry, increasing realized slippage
            # and round-trip rate. Effect persists across populated cohorts.
            #
            # Fail-open if dex_id couldn't be determined.
            # Also blocks DEX-Orca pools (added 2026-05-10): the bot's
            # SLUG_MAP for chart fetches doesn't include orca, so chart
            # data fetches fail through; bot also picks up the ORCA
            # governance token (low-vol blue chip, no momentum) when its
            # 1m signature triggers clean_break. Block both.
            try:
                _grad_dex = (_graduation_dict or {}).get("graduation_dex_id")
                if _grad_dex == "meteora":
                    logger.info(
                        f"[DipScanner] BLOCKED by filter_meteora_dex: {token_symbol} "
                        f"graduation_dex_id=meteora (DLMM round-trip risk)"
                    )
                    c["filter_meteora_dex_block"] = c.get("filter_meteora_dex_block", 0) + 1
                    if not _user_watch:
                        continue
                    logger.info(f"[DipScanner] WATCHLIST BYPASS filter_meteora_dex: {token_symbol}")
                if _grad_dex == "orca":
                    logger.info(
                        f"[DipScanner] BLOCKED by filter_orca_dex: {token_symbol} "
                        f"graduation_dex_id=orca (unsupported DEX — chart plumbing falls through)"
                    )
                    c["filter_orca_dex_block"] = c.get("filter_orca_dex_block", 0) + 1
                    continue
            except NameError:
                pass  # _graduation_dict not built — fail-open
            c["filter_meteora_dex_pass"] = c.get("filter_meteora_dex_pass", 0) + 1

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

            # filter_bs_m5_weak — ENFORCED 2026-05-12.
            # Surgical block on bs_m5 < 1.0 entries that LACK rescue signals.
            # Lifetime SHADOW bs_m5<1.40 was net-positive (64% WR/BLOCK), so
            # blunt enforcement loses winners. But on recent 5d (post-stop-
            # tightening), the bs_m5<1.0 cohort net -$45.82 (60W +$67.58 /
            # 46L -$113.40 — losers avg -$2.47 vs winners avg +$1.13).
            #
            # Cohort feature mining (Cohen's d on winners vs losers within
            # bs_m5<1.0) found: unique_buyers_n and net_flow_15s_n are the
            # cleanest separators. Rescue logic: block only when BOTH
            # absent. Keeps 37 of 60 winners (62%) while blocking 35 of
            # 46 losers (76%). Net: +$58.05 saved / +$11/day on 5d data
            # vs +$45.82 from a blunt-block (12% better).
            #
            # Features are 100% covered in the 106-trade cohort. Both
            # come from _trade_log_dict (trade_log_features.analyze on
            # recent_trades).
            _filter_bs_m5_weak_block_reasons: list = []
            try:
                _bsw_val = float(ratio_m5) if ratio_m5 != float("inf") else None
                if _bsw_val is not None and _bsw_val < 1.0:
                    _ub_n = _trade_log_dict.get('unique_buyers_n') or 0
                    _nf15 = _trade_log_dict.get('net_flow_15s_n') or 0
                    if _ub_n < 12 and _nf15 < 4:
                        _filter_bs_m5_weak_block_reasons.append(
                            f"bs_m5={_bsw_val:.2f}<1.0 AND no rescue "
                            f"(unique_buyers_n={_ub_n}<12 AND net_flow_15s_n={_nf15}<4)"
                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] bs_m5_weak calc err: {_e}")
            # CARVE-OUT 2026-05-16 PM: rescue when pc_m5 >= -0.60.
            # Mining: bs_m5_weak-blocked + pc_m5>=-0.60 → n=73, 62% won_10pct
            # (+16.5% avg peak) vs 50% blocks-baseline. The "weak m5 buy/sell
            # ratio" thesis fails when m5 already stabilized (not actively
            # selling). bs_m5 lags pc_m5 in fast-moving tokens.
            _bs_m5_weak_carve = False
            if (_filter_bs_m5_weak_block_reasons
                and pc_m5 is not None and float(pc_m5) >= -0.60):
                _bs_m5_weak_carve = True
            # BREAKTHROUGH carve-out 2026-05-16 PM: rescue when early
            # breakthrough flag matches (strong_orderflow or sustained_
            # accumulation present at this point). Those triggers had
            # 8/8 and 7/7 WR on lifetime data — bs_m5 weakness is
            # downstream noise when multi-window flow is already
            # validated positive.
            _bs_m5_weak_breakthrough_carve = False
            if (_filter_bs_m5_weak_block_reasons
                and not _bs_m5_weak_carve
                and _breakthrough_early_match):
                _bs_m5_weak_breakthrough_carve = True
            _filter_bs_m5_weak_verdict = (
                "BLOCK" if (_filter_bs_m5_weak_block_reasons
                            and not _bs_m5_weak_carve
                            and not _bs_m5_weak_breakthrough_carve)
                else "PASS"
            )
            c[f"filter_bs_m5_weak_{_filter_bs_m5_weak_verdict.lower()}"] = c.get(
                f"filter_bs_m5_weak_{_filter_bs_m5_weak_verdict.lower()}", 0
            ) + 1
            if _filter_bs_m5_weak_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_bs_m5_weak: {token_symbol} "
                    f"reasons={','.join(_filter_bs_m5_weak_block_reasons)}"
                )
                if not _user_watch:
                    continue
                logger.info(f"[DipScanner] WATCHLIST BYPASS filter_bs_m5_weak: {token_symbol}")
            if _bs_m5_weak_carve and _filter_bs_m5_weak_block_reasons:
                logger.info(
                    f"[DipScanner] filter_bs_m5_weak RESCUED by pc_m5={float(pc_m5):+.2f}%>=-0.60: "
                    f"{token_symbol}"
                )
                c["filter_bs_m5_weak_carve_pc_m5"] = c.get("filter_bs_m5_weak_carve_pc_m5", 0) + 1
            if _bs_m5_weak_breakthrough_carve:
                logger.info(
                    f"[DipScanner] filter_bs_m5_weak RESCUED by breakthrough early flag: "
                    f"{token_symbol}"
                )
                c["filter_bs_m5_weak_carve_breakthrough"] = c.get(
                    "filter_bs_m5_weak_carve_breakthrough", 0
                ) + 1

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

            # ────────────────────────────────────────────────────────────
            # NEW PARALLEL TRIGGERS — ENFORCED 2026-05-12
            # Eight orthogonal entry families mined from 1,695 rejected
            # candidates (phantom verdicts S_live_prod_stack=BLOCK).
            # Each fires INDEPENDENTLY of clean_break + high_regime.
            #
            # Validation: missed-winner pool n=382, missed-losers n=329.
            # Union projects +130% volume at 70% WR / +2.66% avg pnl.
            # Full audit in .orthogonal_trigger_research.md and
            # .creative_trigger_research.md.
            # ────────────────────────────────────────────────────────────
            _trigger_alpha_buyperscold_match = False
            _trigger_alpha_buyperscold_reasons: list = []
            _trigger_beta_retailfresh_match = False
            _trigger_beta_retailfresh_reasons: list = []
            _trigger_delta_microcap_match = False
            _trigger_delta_microcap_reasons: list = []
            _trigger_seller_exhaustion_match = False
            _trigger_seller_exhaustion_reasons: list = []
            _trigger_deep_dip_bottom_match = False
            _trigger_deep_dip_bottom_reasons: list = []
            _trigger_patient_bottom_match = False
            _trigger_patient_bottom_reasons: list = []
            _trigger_informed_cluster_match = False
            _trigger_informed_cluster_reasons: list = []
            _trigger_grad_window_dip_match = False
            _trigger_grad_window_dip_reasons: list = []
            try:
                _bs_m5_f = float(ratio_m5) if ratio_m5 != float("inf") else None
                _vwap_dist = _tier2_features.get("pct_above_vwap_1h")
                _min_peak = _tier2_features.get("minutes_since_peak")
                _top10_60s = _tier2_features.get("top10_buyer_within_60s_count")
                _hours_grad = _tier3_features.get("hours_since_graduation")
                _slip_buy = jup_features.get("slip_buy_5000_pct") if isinstance(jup_features, dict) else None
                _slip_sell = jup_features.get("slip_sell_5000_pct") if isinstance(jup_features, dict) else None
                _slip_sell_vel = None
                try:
                    _slip_sell_vel = slip_ts_features.get("slip_sell_5k_velocity_pct_per_min")
                except NameError:
                    pass
                _pc24_f = float(pc_h24) if pc_h24 is not None else 0.0
                _peak24_6h_f = float(peak_h24_6h) if peak_h24_6h is not None else 0.0
                _ats = float(avg_trade_size_h1) if avg_trade_size_h1 is not None else 0.0
                _p5r = float(pct_in_5m_range) if pct_in_5m_range is not None else None
                _vh1 = float(vol_h1) if vol_h1 is not None else 0.0
                _mc = float(mcap) if mcap is not None else 0.0

                # Seller-active gate — fail-open if missing, otherwise require
                # net_flow_60s_imbalance >= -0.3 (sellers not actively winning
                # the last 60s). Scoped to new triggers only — 7d held-out
                # in-scope showed +22% Delta-WR (BLOCK 14% / ALLOW 36%).
                # Without this gate the new triggers fire on tokens with -0.5
                # imbalance even when chart screams active selling (GPXY56UAL
                # 2026-05-12 14:21 reference incident, lost -$2.03 in 33s).
                _nfi_for_trigger = _tier3_features.get("net_flow_60s_imbalance")
                _seller_active = (_nfi_for_trigger is not None
                                  and _nfi_for_trigger < -0.3)

                # ALPHA — strong 5m buy pressure on non-runaway token
                if _bs_m5_f is not None and _bs_m5_f >= 3.0 and _pc24_f < 50 and not _seller_active:
                    _trigger_alpha_buyperscold_match = True
                    _trigger_alpha_buyperscold_reasons.append(
                        f"bs_m5={_bs_m5_f:.2f}>=3.0 AND pc_h24={_pc24_f:+.1f}%<50"
                    )

                # BETA — retail-sized trades + price low in 5m range + no recent runaway peak
                if (_ats > 0 and _ats < 60 and _p5r is not None and _p5r < 0.3
                        and _peak24_6h_f < 40 and not _seller_active):
                    _trigger_beta_retailfresh_match = True
                    _trigger_beta_retailfresh_reasons.append(
                        f"avg_trade=${_ats:.0f}<60 AND pct_in_5m={_p5r:.2f}<0.3 "
                        f"AND peak_h24_6h={_peak24_6h_f:.1f}%<40"
                    )

                # DELTA — microcap with low entry slippage + live volume
                if (0 < _mc < 5_000_000 and _slip_buy is not None and _slip_buy < 3.0
                        and _vh1 > 50_000 and not _seller_active):
                    _trigger_delta_microcap_match = True
                    _trigger_delta_microcap_reasons.append(
                        f"mcap=${_mc/1e6:.1f}M<5M AND slip_buy={_slip_buy:.2f}%<3 "
                        f"AND vol_h1=${_vh1/1e3:.0f}k>50k"
                    )

                # seller_exhaustion — bs + rising sell-side slip + high absolute slip
                if (_bs_m5_f is not None and _bs_m5_f >= 1.34
                        and _slip_sell_vel is not None and _slip_sell_vel >= 0.0004
                        and _slip_sell is not None and _slip_sell >= 2.25
                        and not _seller_active):
                    _trigger_seller_exhaustion_match = True
                    _trigger_seller_exhaustion_reasons.append(
                        f"bs_m5={_bs_m5_f:.2f}>=1.34 AND slip_sell_vel={_slip_sell_vel:.4f}>=0.0004 "
                        f"AND slip_sell={_slip_sell:.2f}%>=2.25"
                    )

                # deep_dip_bottom — token genuinely dipped (down both 24h AND from 6h peak)
                # Phantom predicate ratio_to_recent_peak<=0.928 mapped to peak_h24_6h>=7.2pp
                if _pc24_f <= -7.48 and _peak24_6h_f >= 7.2 and not _seller_active:
                    _trigger_deep_dip_bottom_match = True
                    _trigger_deep_dip_bottom_reasons.append(
                        f"pc_h24={_pc24_f:+.1f}%<=-7.48 AND peak_h24_6h={_peak24_6h_f:.1f}%>=7.2 "
                        f"(deep dip from 6h peak)"
                    )

                # patient_bottom_recovery — well below 1h VWAP, mature dip
                if (_vwap_dist is not None and _vwap_dist <= -3.0
                        and _min_peak is not None and _min_peak >= 60
                        and not _seller_active):
                    _trigger_patient_bottom_match = True
                    _trigger_patient_bottom_reasons.append(
                        f"vwap_1h_dist={_vwap_dist:+.1f}%<=-3 AND min_since_peak={_min_peak:.0f}>=60"
                    )

                # informed_cluster_entry — top10 historical buyers re-entering on dip
                if (_top10_60s is not None and _top10_60s >= 5
                        and _vwap_dist is not None and _vwap_dist <= -3.0
                        and not _seller_active):
                    _trigger_informed_cluster_match = True
                    _trigger_informed_cluster_reasons.append(
                        f"top10_60s={_top10_60s}>=5 AND vwap_1h_dist={_vwap_dist:+.1f}%<=-3"
                    )

                # graduation_window_dip — fresh post-graduation honeymoon dip
                if (_hours_grad is not None and 6 <= _hours_grad < 24
                        and _vwap_dist is not None and _vwap_dist <= -3.0
                        and not _seller_active):
                    _trigger_grad_window_dip_match = True
                    _trigger_grad_window_dip_reasons.append(
                        f"hours_since_grad={_hours_grad:.1f} in [6,24) "
                        f"AND vwap_1h_dist={_vwap_dist:+.1f}%<=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] new-triggers calc err: {_e}")

            # ─── TRIGGER_FEATURES log — 2026-05-14 PM ──────────────────────
            # Emits the gating features for each token's signal so the
            # signal_event_recorder can pair them with eventual BLOCK/BUY
            # outcomes. Used for threshold-relaxation mining (we can audit
            # WR per-feature-bucket on BLOCKED tokens too, not just buys).
            # All values formatted as "key=value" pairs separated by spaces.
            # Null/missing values written as None so the regex matches
            # cleanly.
            try:
                def _fmt(v):
                    if v is None:
                        return "None"
                    if isinstance(v, bool):
                        return str(v)
                    if isinstance(v, (int, float)):
                        return f"{float(v):.3f}"
                    return str(v)
                _tf_top10 = _tier2_features.get("top10_buyer_within_60s_count") if _tier2_features else None
                _tf_vwap_1h = _tier2_features.get("pct_above_vwap_1h") if _tier2_features else None
                _tf_min_peak = _tier2_features.get("minutes_since_peak") if _tier2_features else None
                _tf_hr_grad = _tier3_features.get("hours_since_graduation") if _tier3_features else None
                _tf_grad_status = _tier3_features.get("graduation_status") if _tier3_features else None
                _tf_nfi60 = _tier2_features.get("net_flow_60s_imbalance") if _tier2_features else None
                _tf_bp60 = _tier2_features.get("buy_pressure_60s") if _tier2_features else None
                _tf_buy_max = _tier2_features.get("buy_size_max_trend") if _tier2_features else None
                _tf_lvh1 = _tier2_features.get("liq_velocity_h1_usd_per_txn") if _tier2_features else None
                _tf_lc_ratio = (_lifecycle_dict or {}).get("lifecycle_h24_ratio")
                logger.info(
                    f"[DipScanner] TRIGGER_FEATURES: {token_symbol} "
                    f"top10_60s={_fmt(_tf_top10)} vwap_1h={_fmt(_tf_vwap_1h)} "
                    f"min_peak={_fmt(_tf_min_peak)} hr_grad={_fmt(_tf_hr_grad)} "
                    f"grad_status={_fmt(_tf_grad_status)} "
                    f"nfi_60s={_fmt(_tf_nfi60)} bp_60s={_fmt(_tf_bp60)} "
                    f"buy_max_trend={_fmt(_tf_buy_max)} "
                    f"liq_vel_h1={_fmt(_tf_lvh1)} "
                    f"h24_ratio={_fmt(_tf_lc_ratio)}"
                )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_features log err: {_e}")

            # demand_bottom_compound — ENFORCED 2026-05-13.
            # Trimmed 3-branch union (P1 dropped — redundant with current
            # filter stack catches). Mines microstructure demand-stepping-up
            # signals rather than chart-shape patterns, which generalize
            # better across regimes.
            #
            # Three branches:
            #   B1: buy_size_max_trend >= 2.0 AND peak_h24_6h_pct >= 500
            #       Post-pump token with demand stepping up.
            #   B2: graduation_status == 'just_graduated' AND
            #       buy_size_max_trend >= 2.0
            #       Fresh pump.fun graduate with demand stepping up.
            #   B3: chart_sweep_5m_verdict == 'BULLISH_SWEEP' AND
            #       peak_h24_6h_pct >= 500 AND chart_score >= 50
            #       Confirmed bullish sweep on a recently-pumped token.
            #
            # Validation (TRAIN 5/5-5/8 + VAL 5/9-5/11):
            #   Pre-filter union: TRAIN 42/69%, VAL 12/83% — generalized.
            # After-current-filter-stack on 7d (post-2026-05-13 stack):
            #   16 clean fires, 14W/2L = 88% WR, +$12.64 total.
            #   9 marginal new entries (no clean_break overlap): 9/9 = 100% WR.
            # Expected forward: ~2/day fires, ~1.3/day marginal new entries,
            # ~85-90% WR.
            # trigger_sweep_rejection — ENFORCED 2026-05-13.
            # Catches the "big lower wick on 5m sweep low" pattern: when
            # chart_sweep_5m_low_wick_pct >= 4 (lower wick is 4%+ of price)
            # = sellers swept lower lows and buyers absorbed hard.
            # Big-winner Cohen's d separation: 5.5% vs 2.0% (d=0.80).
            #
            # Validated (TRAIN 5/5-5/8 + VAL 5/9-5/11):
            #   TRAIN: 28 fires, 82% WR, +$51.9
            #   VAL:   8 fires, 62% WR, +$0.1
            #   Combined ~5/day, 77% WR on 7d
            # NOTE: lp_locked_pct>=90 (which would lift WR to TRAIN 89% /
            # VAL 71%) is only available post-rugcheck in trader.buy. Can
            # be added as a confirmation filter there if WR drifts.
            _trigger_sweep_rejection_match = False
            _trigger_sweep_rejection_reasons: list = []
            try:
                _swp_wick = (_chart_ctx_dict or {}).get("chart_sweep_5m_low_wick_pct") if isinstance(_chart_ctx_dict, dict) else None
                # RETUNED 2026-05-13 PM after IDLE loss (-$1.70, 6min hold).
                # Original predicate (wick>=4 alone) had 34% WR on lifetime
                # n=44 standalone cohort — earlier validation was contaminated
                # by clean_break co-fires. Tightened with two structural gates:
                #   pct_above_vwap_h24 <= 10  (not chasing a pump — at/below
                #                              24h VWAP = real discount zone)
                #   pct_in_5m_range >= 0.5    (price already in upper half of
                #                              5m range = turning point formed)
                # Lifetime validation w/ gates: 12 fires, 75% WR, +$12.7,
                # +$1.06/trade. TRAIN 9/78% / VAL 3/67% — direction holds.
                # IDLE replay: blocked on BOTH gates (vwap_h24=+56.5, p5r=0.10).
                _swp_vwap_h24 = None
                _swp_p5r = None
                try:
                    _swp_vwap_h24 = float(vwap_features.get("pct_above_vwap_h24")) if vwap_features.get("pct_above_vwap_h24") is not None else None
                except Exception:
                    _swp_vwap_h24 = None
                try:
                    _swp_p5r = float(pct_in_5m_range) if pct_in_5m_range is not None else None
                except Exception:
                    _swp_p5r = None
                if (_swp_wick is not None and float(_swp_wick) >= 4.0
                        and _swp_vwap_h24 is not None and _swp_vwap_h24 <= 10.0
                        and _swp_p5r is not None and _swp_p5r >= 0.5):
                    _trigger_sweep_rejection_match = True
                    _trigger_sweep_rejection_reasons.append(
                        f"chart_sweep_5m_low_wick_pct={float(_swp_wick):.2f}%>=4 "
                        f"AND pct_above_vwap_h24={_swp_vwap_h24:+.1f}%<=10 "
                        f"AND pct_in_5m_range={_swp_p5r:.2f}>=0.5"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] sweep_rejection trigger err: {_e}")

            # trigger_reaccum_demand — ENFORCED 2026-05-13.
            # Catches reaccumulation-zone entries with demand confirmation:
            # chart_reaccum_drawdown_pct >= 50 (token drew down 50%+ from
            # recent peak, now consolidating) AND buy_size_max_trend >= 2.0
            # (max single-buy size in last 60s is 2x+ prior 60s).
            #
            # Validated (TRAIN 5/5-5/8 + VAL 5/9-5/11):
            #   TRAIN: 16 fires, 69% WR, +$10.5
            #   VAL:   8 fires, 75% WR, +$0.8
            #   Combined ~3-4/day, 72% WR — stable across regimes
            # Big-winner Cohen's d on reaccum_drawdown: 48% vs 34% (d=0.67).
            _trigger_reaccum_demand_match = False
            _trigger_reaccum_demand_reasons: list = []
            try:
                _ra_dd = (_chart_ctx_dict or {}).get("chart_reaccum_drawdown_pct") if isinstance(_chart_ctx_dict, dict) else None
                _bst_ra = None
                try:
                    _bst_ra = (_trade_log_dict or {}).get("buy_size_max_trend")
                except Exception:
                    _bst_ra = None
                # RETUNED 2026-05-13 PM after audit revealed VAL collapse
                # (TRAIN 67% -> VAL 44%) and toxic co-fires with VWAP-cluster
                # triggers (grad_window_dip / informed_cluster / patient_bottom
                # all at 0% WR). Added structural gate:
                #   h24_ratio_to_peak < 0.6 (token currently at <60% of 24h
                #     peak = real dip from top, not recovered to near-peak
                #     where reaccum-drawdown shape misleads).
                # Lifetime validation w/ gate: 18 fires, 87% WR, +$17.2 total.
                # TRAIN (5/6-5/10): 13 fires, 92% WR, +$14.7.
                # VAL (5/10-5/12): 5 fires, 80% WR, +$2.5. Direction holds.
                _ra_h24_ratio = (pc_h24 / float(peak_h24_6h)) if peak_h24_6h is not None and float(peak_h24_6h) > 0 else 1.0
                if (_ra_dd is not None and float(_ra_dd) >= 50.0
                        and _bst_ra is not None and float(_bst_ra) >= 2.0
                        and _ra_h24_ratio < 0.6):
                    _trigger_reaccum_demand_match = True
                    _trigger_reaccum_demand_reasons.append(
                        f"reaccum_dd={float(_ra_dd):.0f}%>=50 "
                        f"AND buy_size_max_trend={float(_bst_ra):.2f}>=2 "
                        f"AND h24_ratio_to_peak={_ra_h24_ratio:.2f}<0.6"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] reaccum_demand trigger err: {_e}")

            # ── trigger_extreme_sweep_1m — ENFORCED 2026-05-13 PM ──────────────
            # New entry derived from deep candle synthesis (n=86,865 candles
            # across 37 tokens, 12 timeframes). Catches "panic flush rejected
            # by buyers" pattern: any 1m bar in last 5 with lower_wick / body
            # >= 10 (with body > 0). Sample evidence: 3 of 14 winners had
            # this signature, 0 of 15 losers — 100% precision on tuning set.
            # Winners: 5o61DgZrDGbC (MID_WIN +0.89), Gps4KFPSP9Wc (BIG_WIN
            # +1.38), Hf8RNuWd4DLv (MID_WIN +1.13).
            #
            # GATED by lifecycle_peak_h24_pct (peak_h24_6h) >= 200%. The gate
            # is scoped to this trigger ONLY — existing triggers untouched.
            # Mechanism: only chase 24h-active tokens (need real range to
            # capture the +5% TP1 move).
            _trigger_extreme_sweep_1m_match = False
            _trigger_extreme_sweep_1m_reasons: list = []
            try:
                _esw_cs = (_chart_data.candles_1m
                           if _chart_data and _chart_data.candles_1m else [])
                if len(_esw_cs) >= 5:
                    _esw_last5 = _esw_cs[-5:]
                    _esw_max_ratio = 0.0
                    _esw_max_idx = -1
                    for _i, _c in enumerate(_esw_last5):
                        _body = abs(_c.close - _c.open)
                        if _body <= 0:
                            continue
                        _lw = min(_c.open, _c.close) - _c.low
                        if _lw <= 0:
                            continue
                        _r = _lw / _body
                        if _r > _esw_max_ratio:
                            _esw_max_ratio = _r
                            _esw_max_idx = _i
                    # Gate: requires 24h_peak >= 200% (lifecycle peak field
                    # is populated 100% for tokens with DexScreener data).
                    _esw_peak = float(peak_h24_6h) if peak_h24_6h is not None else 0.0
                    if _esw_max_ratio >= 10.0 and _esw_peak >= 200.0:
                        _trigger_extreme_sweep_1m_match = True
                        _trigger_extreme_sweep_1m_reasons.append(
                            f"1m_max_wick_body_ratio={_esw_max_ratio:.1f}>=10 "
                            f"(bar -{4 - _esw_max_idx}) AND "
                            f"peak_h24_6h={_esw_peak:.0f}%>=200"
                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] extreme_sweep_1m trigger err: {_e}")

            # ── trigger_liq_velocity_big_buyers — ENFORCED 2026-05-13 PM ─────
            # Round-7 exhaustive entry_meta mining (576 features tested at
            # multiple thresholds on n=29 paired). liq_velocity_h1_usd_per_txn
            # was the strongest discriminator: 6W/0L at threshold $135/txn.
            # Predicate: avg $ per txn over last 1h >= $135 (big-buyer
            # presence — high-conviction txn sizes).
            _trigger_liq_velocity_match = False
            _trigger_liq_velocity_reasons: list = []
            try:
                _lv_h1 = volume_velocity_features.get("liq_velocity_h1_usd_per_txn")
                if _lv_h1 is not None and float(_lv_h1) >= 135.0:
                    _trigger_liq_velocity_match = True
                    _trigger_liq_velocity_reasons.append(
                        f"liq_velocity_h1_usd_per_txn=${float(_lv_h1):.0f}/txn>=135 "
                        f"(big-buyer presence)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] liq_velocity trigger err: {_e}")

            # ── trigger_net_flow_5m_demand — ENFORCED 2026-05-13 PM ──────────
            # Round-7: net_flow_5m_usd >= $300 → 5W/0L = 100% precision.
            # Predicate: 5-minute USD net buy flow >= $300 (sustained pressure).
            _trigger_net_flow_5m_match = False
            _trigger_net_flow_5m_reasons: list = []
            try:
                _nf5m = _tier3_features.get("net_flow_5m_usd") if isinstance(_tier3_features, dict) else None
                if _nf5m is not None and float(_nf5m) >= 300.0:
                    _trigger_net_flow_5m_match = True
                    _trigger_net_flow_5m_reasons.append(
                        f"net_flow_5m_usd=${float(_nf5m):+.0f}>=300 "
                        f"(sustained 5m net buy pressure)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] net_flow_5m trigger err: {_e}")

            # ── trigger_modest_pump_deep_retrace — ENFORCED 2026-05-14 PM ──
            # MASCOTS pattern (won +$0.46 TP1 + $0.06 trail on 2026-05-14):
            # token with modest 24h pump (~100%) that has deeply retraced
            # (lifecycle_ratio<0.10 = price at <10% of 24h peak). Different
            # from existing demand_bottom_compound branch (which requires
            # peak>=500%). Catches the "small-pump dead-cat that has fully
            # bled out and is now re-accumulating" pattern.
            # Mining audit (.dataset.pkl 7d): n=6, 66.7% WR, total +$3.94.
            # Tighter ratio<0.05 sub-cohort: n=5, 80% WR.
            # No additional gates (mining data didn't have bs_h6/net_flow
            # available in this cohort to layer cleanly).
            _trigger_modest_pump_deep_retrace_match = False
            _trigger_modest_pump_deep_retrace_reasons: list = []
            try:
                _mpdr_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _mpdr_ratio = (_lifecycle_dict or {}).get("lifecycle_h24_ratio")
                if (
                    _mpdr_peak is not None and 50 <= _mpdr_peak < 150
                    and isinstance(_mpdr_ratio, (int, float))
                    and _mpdr_ratio < 0.10
                ):
                    _trigger_modest_pump_deep_retrace_match = True
                    _trigger_modest_pump_deep_retrace_reasons.append(
                        f"peak_h24_6h={_mpdr_peak:.0f}% in [50,150) AND "
                        f"h24_ratio={_mpdr_ratio:.3f}<0.10 "
                        f"(modest pump deeply retraced)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] modest_pump_deep_retrace err: {_e}")

            # ── trigger_small_pump_shallow_retrace — ENFORCED 2026-05-14 PM ──
            # Highest-EV cohort found in MASCOTS-pattern mining:
            # peak_h24 in [25, 50) AND lifecycle_ratio in [0.60, 0.80).
            # Mining audit (.dataset.pkl 7d): n=56, 66.1% WR, total +$418.8
            # (avg +$7.48/trade — far above baseline avg +$0.11/trade).
            # Mechanism: small 24h pump (25-50%) where price is still ~60-80%
            # of the peak — token in active uptrend with a shallow pullback,
            # NOT a post-pump corpse. Distinct from MASCOTS' deep-retrace
            # pattern. Per-day fire estimate: ~8.
            _trigger_small_pump_shallow_retrace_match = False
            _trigger_small_pump_shallow_retrace_reasons: list = []
            try:
                _spsr_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _spsr_ratio = (_lifecycle_dict or {}).get("lifecycle_h24_ratio")
                if (
                    _spsr_peak is not None and 25 <= _spsr_peak < 50
                    and isinstance(_spsr_ratio, (int, float))
                    and 0.60 <= _spsr_ratio < 0.80
                ):
                    _trigger_small_pump_shallow_retrace_match = True
                    _trigger_small_pump_shallow_retrace_reasons.append(
                        f"peak_h24_6h={_spsr_peak:.0f}% in [25,50) AND "
                        f"h24_ratio={_spsr_ratio:.3f} in [0.60,0.80) "
                        f"(small pump, shallow retrace = active uptrend pullback)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] small_pump_shallow_retrace err: {_e}")

            # ── trigger_shallow_retrace_fresh_pump — ENFORCED 2026-05-14 PM ──
            # Exhaustive-mining 3D goldmine: peak[25,50) AND ratio[0.70,0.85)
            # AND cycles_seen[10,30). Audit n=12, 91.7% WR, +$194 total
            # ($16.18/trade avg — highest $/trade of all mined cohorts).
            # Mechanism: small-pump token in shallow pullback, discovered
            # by bot in the last 10-30 cycles (fresh but not unseen).
            _trigger_shallow_retrace_fresh_pump_match = False
            _trigger_shallow_retrace_fresh_pump_reasons: list = []
            try:
                _srfp_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _srfp_ratio = (_lifecycle_dict or {}).get("lifecycle_h24_ratio")
                _srfp_cycles = cycles_seen
                if (
                    _srfp_peak is not None and 25 <= _srfp_peak < 50
                    and isinstance(_srfp_ratio, (int, float))
                    and 0.70 <= _srfp_ratio < 0.85
                    and isinstance(_srfp_cycles, (int, float))
                    and 10 <= _srfp_cycles < 30
                ):
                    _trigger_shallow_retrace_fresh_pump_match = True
                    _trigger_shallow_retrace_fresh_pump_reasons.append(
                        f"peak={_srfp_peak:.0f}% AND ratio={_srfp_ratio:.2f} AND "
                        f"cycles={int(_srfp_cycles)} (3D goldmine n=12, 91.7%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] shallow_retrace_fresh_pump err: {_e}")

            # ── trigger_midcap_quality_accumulation — ENFORCED 2026-05-14 PM ──
            # mcap[$2M,$10M) AND bs_h6[1.1,1.3) AND ratio[0.5,0.7).
            # Audit n=35, 82.9% WR, +$151. Mechanism: midcap token with
            # mild sustained accumulation (bs_h6 in quality band), price
            # in middle of 24h range — slow-builder pattern.
            _trigger_midcap_quality_accumulation_match = False
            _trigger_midcap_quality_accumulation_reasons: list = []
            try:
                _mqa_mc = float(mcap) if mcap is not None else None
                _mqa_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _mqa_ratio = (_lifecycle_dict or {}).get("lifecycle_h24_ratio")
                if (
                    _mqa_mc is not None and 2_000_000 <= _mqa_mc < 10_000_000
                    and _mqa_bsh6 is not None and 1.1 <= _mqa_bsh6 < 1.3
                    and isinstance(_mqa_ratio, (int, float))
                    and 0.5 <= _mqa_ratio < 0.7
                ):
                    _trigger_midcap_quality_accumulation_match = True
                    _trigger_midcap_quality_accumulation_reasons.append(
                        f"mcap=${_mqa_mc/1e6:.1f}M AND bs_h6={_mqa_bsh6:.2f} AND "
                        f"ratio={_mqa_ratio:.2f} (n=35, 82.9%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] midcap_quality_accumulation err: {_e}")

            # ── trigger_fresh_graduate_buyers — ENFORCED 2026-05-14 PM ──
            # age_hours[6,24) AND bs_h1[1.3,1.6). Audit n=52, 75.0% WR, +$135.
            # Mechanism: freshly-graduated pump.fun token (6-24h post-grad)
            # with moderate 1h buy pressure. Largest sample of any 75%+ WR
            # cohort — most reliable.
            _trigger_fresh_graduate_buyers_match = False
            _trigger_fresh_graduate_buyers_reasons: list = []
            try:
                _fgb_age = entry_age_hours if "entry_age_hours" in dir() else None
                # Fallback: compute from token data if available
                if _fgb_age is None:
                    _fgb_age = (_tier3_features or {}).get("hours_since_graduation")
                _fgb_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                if (
                    isinstance(_fgb_age, (int, float))
                    and 6 <= _fgb_age < 24
                    and _fgb_bsh1 is not None and 1.3 <= _fgb_bsh1 < 1.6
                ):
                    _trigger_fresh_graduate_buyers_match = True
                    _trigger_fresh_graduate_buyers_reasons.append(
                        f"age={_fgb_age:.1f}h AND bs_h1={_fgb_bsh1:.2f} "
                        f"(n=52, 75.0%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] fresh_graduate_buyers err: {_e}")

            # ── trigger_small_pump_fresh_cycles — ENFORCED 2026-05-14 PM ──
            # peak[25,50) AND cycles_seen[10,30) AND avg_trade_size[$200,$500).
            # Audit n=10, 90.0% WR, +$87 ($8.70/trade). Mechanism: small-pump
            # fresh-discovery token with mid-size trades (institutional, not
            # retail-fomo). Smallest n but strong $/trade.
            _trigger_small_pump_fresh_cycles_match = False
            _trigger_small_pump_fresh_cycles_reasons: list = []
            try:
                _spfc_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _spfc_cycles = cycles_seen
                _spfc_ats = float(avg_trade_size_h1) if avg_trade_size_h1 is not None else None
                if (
                    _spfc_peak is not None and 25 <= _spfc_peak < 50
                    and isinstance(_spfc_cycles, (int, float))
                    and 10 <= _spfc_cycles < 30
                    and _spfc_ats is not None and 200 <= _spfc_ats < 500
                ):
                    _trigger_small_pump_fresh_cycles_match = True
                    _trigger_small_pump_fresh_cycles_reasons.append(
                        f"peak={_spfc_peak:.0f}% AND cycles={int(_spfc_cycles)} AND "
                        f"avg_trade=${_spfc_ats:.0f} (n=10, 90.0%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] small_pump_fresh_cycles err: {_e}")

            # ── trigger_midcap_bigpump_fresh — ENFORCED 2026-05-14 PM ──
            # mcap[$2M,$10M) AND bs_h6[1.1,1.3) AND peak_h24[1000+).
            # Audit n=28, 89.3% WR, +$92. Mechanism: midcap with mild
            # accumulation AND massive 24h pump still in play.
            _trigger_midcap_bigpump_fresh_match = False
            _trigger_midcap_bigpump_fresh_reasons: list = []
            try:
                _mbf_mc = float(mcap) if mcap is not None else None
                _mbf_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _mbf_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                if (
                    _mbf_mc is not None and 2_000_000 <= _mbf_mc < 10_000_000
                    and _mbf_bsh6 is not None and 1.1 <= _mbf_bsh6 < 1.3
                    and _mbf_peak is not None and _mbf_peak >= 1000
                ):
                    _trigger_midcap_bigpump_fresh_match = True
                    _trigger_midcap_bigpump_fresh_reasons.append(
                        f"mcap=${_mbf_mc/1e6:.1f}M AND bs_h6={_mbf_bsh6:.2f} AND "
                        f"peak={_mbf_peak:.0f}%>=1000 (n=28, 89.3%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] midcap_bigpump_fresh err: {_e}")

            # Helper: is hour_ct in the full overnight band [19,24) ∪ [0,7)?
            try:
                from zoneinfo import ZoneInfo as _ZI
                _ovn_h = datetime.now(_ZI("America/Chicago")).hour
                _ovn_active = (19 <= _ovn_h < 24) or (0 <= _ovn_h < 7)
            except Exception:
                _ovn_h = -1
                _ovn_active = False

            # ── trigger_overnight_modest_pump_consol — ENFORCED 2026-05-14 PM ─
            # LOOSENED 2026-05-14 evening from [3,7) to full overnight [19,7).
            # Overnight-edge cohort from mine_overnight_cohorts.py: em_bs_h6
            # in [1.1,1.3) AND peak_h24_6h_pct in [25,50) AND hour_ct in
            # overnight band. Lifetime audit (.dataset.pkl, 19-7 CT slice):
            # n=24, 75.0% WR, +$224 vs daytime 41.2% WR, +$98 (Δ +33.8pp).
            _trigger_overnight_modest_pump_consol_match = False
            _trigger_overnight_modest_pump_consol_reasons: list = []
            try:
                _ompc_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _ompc_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                if (
                    _ovn_active
                    and _ompc_peak is not None and 25 <= _ompc_peak < 50
                    and _ompc_bsh6 is not None and 1.1 <= _ompc_bsh6 < 1.3
                ):
                    _trigger_overnight_modest_pump_consol_match = True
                    _trigger_overnight_modest_pump_consol_reasons.append(
                        f"hour_ct={_ovn_h} in [19,7) AND peak={_ompc_peak:.0f}% "
                        f"AND bs_h6={_ompc_bsh6:.2f} (overnight n=24, 75.0%WR, Δ+33.8pp)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_modest_pump_consol err: {_e}")

            # ── trigger_overnight_quiet_accumulation — ENFORCED 2026-05-14 PM ─
            # LOOSENED 2026-05-14 evening from [3,7) to full overnight [19,7).
            # avg_trade_size_h1 in [$60,$100) AND cycles_seen in [30,60).
            # Overnight n=47, 74.5% WR, +$116; daytime n=14, 42.9% WR, -$129
            # (Δ +31.6pp, day is a LOSER — cleanest inversion in mining).
            _trigger_overnight_quiet_accumulation_match = False
            _trigger_overnight_quiet_accumulation_reasons: list = []
            try:
                _oqa_ats = float(avg_trade_size_h1) if avg_trade_size_h1 is not None else None
                _oqa_cyc = cycles_seen
                if (
                    _ovn_active
                    and _oqa_ats is not None and 60 <= _oqa_ats < 100
                    and isinstance(_oqa_cyc, (int, float))
                    and 30 <= _oqa_cyc < 60
                ):
                    _trigger_overnight_quiet_accumulation_match = True
                    _trigger_overnight_quiet_accumulation_reasons.append(
                        f"hour_ct={_ovn_h} in [19,7) AND avg_trade=${_oqa_ats:.0f} "
                        f"AND cycles={int(_oqa_cyc)} (overnight n=47, 74.5%WR, Δ+31.6pp)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_quiet_accumulation err: {_e}")

            # ── trigger_overnight_fresh_small_pump — ENFORCED 2026-05-14 PM ─
            # Rank-1 from mine_overnight_cohorts.py: peak_h24_6h in [25,50)
            # AND cycles_seen in [10,30). Overnight n=14, 71.4% WR, +$226
            # vs daytime 48.0% WR, +$175 (Δ +23.4pp). Mechanism: small-pump
            # token freshly discovered (10-30 cycles seen) in overnight band.
            _trigger_overnight_fresh_small_pump_match = False
            _trigger_overnight_fresh_small_pump_reasons: list = []
            try:
                _ofsp_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _ofsp_cyc = cycles_seen
                if (
                    _ovn_active
                    and _ofsp_peak is not None and 25 <= _ofsp_peak < 50
                    and isinstance(_ofsp_cyc, (int, float))
                    and 10 <= _ofsp_cyc < 30
                ):
                    _trigger_overnight_fresh_small_pump_match = True
                    _trigger_overnight_fresh_small_pump_reasons.append(
                        f"hour_ct={_ovn_h} in [19,7) AND peak={_ofsp_peak:.0f}% "
                        f"AND cycles={int(_ofsp_cyc)} (overnight n=14, 71.4%WR, Δ+23.4pp)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_fresh_small_pump err: {_e}")

            # ── trigger_overnight_quality_old — ENFORCED 2026-05-14 PM ──────
            # Rank-3 from mining: bs_h6 in [1.1,1.3) AND entry_age_hours
            # >= 720h (~30 days). Overnight n=66 (largest sample!), 63.6%
            # WR, +$189 vs daytime 43.3% WR, +$291 (Δ +20.4pp).
            # Mechanism: mature, established memecoin with mild but
            # sustained 6h buy pressure during overnight hours.
            _trigger_overnight_quality_old_match = False
            _trigger_overnight_quality_old_reasons: list = []
            try:
                _oqo_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _oqo_age = entry_age_hours if "entry_age_hours" in dir() else None
                if _oqo_age is None:
                    _oqo_age = (_tier3_features or {}).get("hours_since_graduation")
                if (
                    _ovn_active
                    and _oqo_bsh6 is not None and 1.1 <= _oqo_bsh6 < 1.3
                    and isinstance(_oqo_age, (int, float)) and _oqo_age >= 720
                ):
                    _trigger_overnight_quality_old_match = True
                    _trigger_overnight_quality_old_reasons.append(
                        f"hour_ct={_ovn_h} in [19,7) AND bs_h6={_oqo_bsh6:.2f} "
                        f"AND age={_oqo_age:.0f}h>=720h (overnight n=66, 63.6%WR, Δ+20.4pp)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_quality_old err: {_e}")

            # ── trigger_overnight_micropump_buyers — ENFORCED 2026-05-14 PM ─
            # Rank-8 from mining: bs_h1 in [0.9,1.1) AND peak_h24_6h in
            # [0,25). Overnight n=25, 60.0% WR, +$111 vs daytime 33.3%
            # WR, +$5 (Δ +26.7pp — biggest WR gap among shipped).
            # Mechanism: small-pump (<25%) token with balanced 1h buy/sell
            # ratio during overnight — finds the actual range traders.
            _trigger_overnight_micropump_buyers_match = False
            _trigger_overnight_micropump_buyers_reasons: list = []
            try:
                _omb_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                _omb_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                if (
                    _ovn_active
                    and _omb_bsh1 is not None and 0.9 <= _omb_bsh1 < 1.1
                    and _omb_peak is not None and 0 <= _omb_peak < 25
                ):
                    _trigger_overnight_micropump_buyers_match = True
                    _trigger_overnight_micropump_buyers_reasons.append(
                        f"hour_ct={_ovn_h} in [19,7) AND bs_h1={_omb_bsh1:.2f} "
                        f"AND peak={_omb_peak:.0f}%<25 (overnight n=25, 60.0%WR, Δ+26.7pp)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_micropump_buyers err: {_e}")

            # ─── 3D-refined overnight triggers — ENFORCED 2026-05-14 PM ───
            # All 5 below derived from PHASE 3 of mine_overnight_cohorts.py
            # by adding a 3rd dimension to the top 2D overnight cohorts. Each
            # tightens a parent 2D cohort to 83-100% WR at the cost of smaller
            # sample (n=9-19). Gated to full overnight band [19,24)∪[0,7).
            # Can fire alongside the 2D parent triggers — that's by design
            # (multiple confirmations stack).

            # ── trigger_overnight_3d_bigpump_fresh_age — n=14, 100% WR ──
            # bs_h6[1.1,1.3) × peak[1000+) × age[6,24h)
            _trigger_overnight_3d_bigpump_fresh_age_match = False
            _trigger_overnight_3d_bigpump_fresh_age_reasons: list = []
            try:
                _3d_a_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _3d_a_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _3d_a_age = entry_age_hours if "entry_age_hours" in dir() else None
                if _3d_a_age is None:
                    _3d_a_age = (_tier3_features or {}).get("hours_since_graduation")
                if (
                    _ovn_active
                    and _3d_a_bsh6 is not None and 1.1 <= _3d_a_bsh6 < 1.3
                    and _3d_a_peak is not None and _3d_a_peak >= 1000
                    and isinstance(_3d_a_age, (int, float)) and 6 <= _3d_a_age < 24
                ):
                    _trigger_overnight_3d_bigpump_fresh_age_match = True
                    _trigger_overnight_3d_bigpump_fresh_age_reasons.append(
                        f"hour_ct={_ovn_h}∈[19,7) AND bs_h6={_3d_a_bsh6:.2f} "
                        f"AND peak={_3d_a_peak:.0f}%>=1000 AND age={_3d_a_age:.0f}h∈[6,24h) "
                        f"(3D ovn n=14, 100%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_3d_bigpump_fresh_age err: {_e}")

            # ── trigger_overnight_3d_bigpump_midcap — n=9, 100% WR ──
            # bs_h6[1.1,1.3) × peak[1000+) × mcap[$2M,$10M)
            _trigger_overnight_3d_bigpump_midcap_match = False
            _trigger_overnight_3d_bigpump_midcap_reasons: list = []
            try:
                _3d_b_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _3d_b_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _3d_b_mc = float(mcap) if mcap is not None else None
                if (
                    _ovn_active
                    and _3d_b_bsh6 is not None and 1.1 <= _3d_b_bsh6 < 1.3
                    and _3d_b_peak is not None and _3d_b_peak >= 1000
                    and _3d_b_mc is not None and 2_000_000 <= _3d_b_mc < 10_000_000
                ):
                    _trigger_overnight_3d_bigpump_midcap_match = True
                    _trigger_overnight_3d_bigpump_midcap_reasons.append(
                        f"hour_ct={_ovn_h}∈[19,7) AND bs_h6={_3d_b_bsh6:.2f} "
                        f"AND peak={_3d_b_peak:.0f}%>=1000 AND mcap=${_3d_b_mc/1e6:.1f}M "
                        f"(3D ovn n=9, 100%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_3d_bigpump_midcap err: {_e}")

            # ── trigger_overnight_3d_midcap_liq_band — n=15, 93.3% WR ──
            # bs_h1[1.1,1.3) × mcap[$2M,$10M) × liq[$100k,$250k)
            _trigger_overnight_3d_midcap_liq_band_match = False
            _trigger_overnight_3d_midcap_liq_band_reasons: list = []
            try:
                _3d_c_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                _3d_c_mc = float(mcap) if mcap is not None else None
                _3d_c_liq = float(liquidity_usd) if "liquidity_usd" in dir() and liquidity_usd is not None else None
                if _3d_c_liq is None:
                    _3d_c_liq = float((pair.get("liquidity") or {}).get("usd") or 0) if "pair" in dir() else None
                if (
                    _ovn_active
                    and _3d_c_bsh1 is not None and 1.1 <= _3d_c_bsh1 < 1.3
                    and _3d_c_mc is not None and 2_000_000 <= _3d_c_mc < 10_000_000
                    and _3d_c_liq is not None and 100_000 <= _3d_c_liq < 250_000
                ):
                    _trigger_overnight_3d_midcap_liq_band_match = True
                    _trigger_overnight_3d_midcap_liq_band_reasons.append(
                        f"hour_ct={_ovn_h}∈[19,7) AND bs_h1={_3d_c_bsh1:.2f} "
                        f"AND mcap=${_3d_c_mc/1e6:.1f}M AND liq=${_3d_c_liq/1000:.0f}k "
                        f"(3D ovn n=15, 93.3%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_3d_midcap_liq_band err: {_e}")

            # ── trigger_overnight_3d_bigpump_avgtrade — n=12, 83.3% WR ──
            # bs_h6[1.1,1.3) × peak[1000+) × avg_trade_size[$100,$200)
            _trigger_overnight_3d_bigpump_avgtrade_match = False
            _trigger_overnight_3d_bigpump_avgtrade_reasons: list = []
            try:
                _3d_d_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _3d_d_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _3d_d_ats = float(avg_trade_size_h1) if avg_trade_size_h1 is not None else None
                if (
                    _ovn_active
                    and _3d_d_bsh6 is not None and 1.1 <= _3d_d_bsh6 < 1.3
                    and _3d_d_peak is not None and _3d_d_peak >= 1000
                    and _3d_d_ats is not None and 100 <= _3d_d_ats < 200
                ):
                    _trigger_overnight_3d_bigpump_avgtrade_match = True
                    _trigger_overnight_3d_bigpump_avgtrade_reasons.append(
                        f"hour_ct={_ovn_h}∈[19,7) AND bs_h6={_3d_d_bsh6:.2f} "
                        f"AND peak={_3d_d_peak:.0f}%>=1000 AND avg_trade=${_3d_d_ats:.0f} "
                        f"(3D ovn n=12, 83.3%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_3d_bigpump_avgtrade err: {_e}")

            # ── trigger_overnight_3d_midcap_mature_cycles — n=19, 89.5% WR ──
            # bs_h1[1.1,1.3) × mcap[$2M,$10M) × cycles[60,150)
            _trigger_overnight_3d_midcap_mature_cycles_match = False
            _trigger_overnight_3d_midcap_mature_cycles_reasons: list = []
            try:
                _3d_e_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                _3d_e_mc = float(mcap) if mcap is not None else None
                _3d_e_cyc = cycles_seen
                if (
                    _ovn_active
                    and _3d_e_bsh1 is not None and 1.1 <= _3d_e_bsh1 < 1.3
                    and _3d_e_mc is not None and 2_000_000 <= _3d_e_mc < 10_000_000
                    and isinstance(_3d_e_cyc, (int, float)) and 60 <= _3d_e_cyc < 150
                ):
                    _trigger_overnight_3d_midcap_mature_cycles_match = True
                    _trigger_overnight_3d_midcap_mature_cycles_reasons.append(
                        f"hour_ct={_ovn_h}∈[19,7) AND bs_h1={_3d_e_bsh1:.2f} "
                        f"AND mcap=${_3d_e_mc/1e6:.1f}M AND cycles={int(_3d_e_cyc)} "
                        f"(3D ovn n=19, 89.5%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_3d_midcap_mature_cycles err: {_e}")

            # ═══ FULL-DAY 3D TRIGGERS — ENFORCED 2026-05-15 ═══════════════════
            # Derived from mine_3d_exhaustive.py: full-dataset 3D scan
            # (30,786 cells, 195 qualifying cohorts). All below are
            # top-ranked ship-worthy (WR >= 85% AND total >= +$25 AND n >= 10).
            # Some include hour gates (validated in specific time windows);
            # others fire 24/7.

            # ── trigger_3d_balanced_h1_fresh_predawn — n=11, 90.9% WR ──
            # bs_h1[0.9,1.1) × cycles[10,30) × hour_ct[4,8). #1 by total $.
            _trigger_3d_balanced_h1_fresh_predawn_match = False
            _trigger_3d_balanced_h1_fresh_predawn_reasons: list = []
            try:
                _fd_a_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                _fd_a_cyc = cycles_seen
                if (
                    4 <= _ovn_h < 8
                    and _fd_a_bsh1 is not None and 0.9 <= _fd_a_bsh1 < 1.1
                    and isinstance(_fd_a_cyc, (int, float)) and 10 <= _fd_a_cyc < 30
                ):
                    _trigger_3d_balanced_h1_fresh_predawn_match = True
                    _trigger_3d_balanced_h1_fresh_predawn_reasons.append(
                        f"hour={_ovn_h}∈[4,8) AND bs_h1={_fd_a_bsh1:.2f} "
                        f"AND cycles={int(_fd_a_cyc)} (3D n=11, 90.9%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_balanced_h1_fresh_predawn err: {_e}")

            # ── trigger_3d_small_pump_shallow_fresh — n=12, 91.7% WR ──
            # peak[25,50) × ratio[0.7,0.85) × cycles[10,30). 24/7. #2 by total $.
            _trigger_3d_small_pump_shallow_fresh_match = False
            _trigger_3d_small_pump_shallow_fresh_reasons: list = []
            try:
                _fd_b_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _fd_b_ratio = (_lifecycle_dict or {}).get("lifecycle_h24_ratio")
                _fd_b_cyc = cycles_seen
                if (
                    _fd_b_peak is not None and 25 <= _fd_b_peak < 50
                    and isinstance(_fd_b_ratio, (int, float)) and 0.7 <= _fd_b_ratio < 0.85
                    and isinstance(_fd_b_cyc, (int, float)) and 10 <= _fd_b_cyc < 30
                ):
                    _trigger_3d_small_pump_shallow_fresh_match = True
                    _trigger_3d_small_pump_shallow_fresh_reasons.append(
                        f"peak={_fd_b_peak:.0f}% AND ratio={_fd_b_ratio:.2f} "
                        f"AND cycles={int(_fd_b_cyc)} (3D 24/7 n=12, 91.7%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_small_pump_shallow_fresh err: {_e}")

            # ── trigger_3d_active_5m_small_pump_fresh — n=10, 90% WR ──
            # bs_m5[1.5,2) × peak[25,50) × cycles[10,30). 24/7.
            _trigger_3d_active_5m_small_pump_fresh_match = False
            _trigger_3d_active_5m_small_pump_fresh_reasons: list = []
            try:
                _fd_c_bsm5 = float(ratio_m5) if ratio_m5 not in (None, float("inf")) else None
                _fd_c_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _fd_c_cyc = cycles_seen
                if (
                    _fd_c_bsm5 is not None and 1.5 <= _fd_c_bsm5 < 2.0
                    and _fd_c_peak is not None and 25 <= _fd_c_peak < 50
                    and isinstance(_fd_c_cyc, (int, float)) and 10 <= _fd_c_cyc < 30
                ):
                    _trigger_3d_active_5m_small_pump_fresh_match = True
                    _trigger_3d_active_5m_small_pump_fresh_reasons.append(
                        f"bs_m5={_fd_c_bsm5:.2f} AND peak={_fd_c_peak:.0f}% "
                        f"AND cycles={int(_fd_c_cyc)} (3D 24/7 n=10, 90.0%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_active_5m_small_pump_fresh err: {_e}")

            # ── trigger_3d_compound_buyers_fresh_age — n=15, 86.7% WR ──
            # bs_h6[1.1,1.3) × bs_h1[1.3,1.6) × age[6,24h). 24/7.
            _trigger_3d_compound_buyers_fresh_age_match = False
            _trigger_3d_compound_buyers_fresh_age_reasons: list = []
            try:
                _fd_d_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _fd_d_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                _fd_d_age = entry_age_hours if "entry_age_hours" in dir() else None
                if _fd_d_age is None:
                    _fd_d_age = (_tier3_features or {}).get("hours_since_graduation")
                if (
                    _fd_d_bsh6 is not None and 1.1 <= _fd_d_bsh6 < 1.3
                    and _fd_d_bsh1 is not None and 1.3 <= _fd_d_bsh1 < 1.6
                    and isinstance(_fd_d_age, (int, float)) and 6 <= _fd_d_age < 24
                ):
                    _trigger_3d_compound_buyers_fresh_age_match = True
                    _trigger_3d_compound_buyers_fresh_age_reasons.append(
                        f"bs_h6={_fd_d_bsh6:.2f} AND bs_h1={_fd_d_bsh1:.2f} "
                        f"AND age={_fd_d_age:.0f}h (3D 24/7 n=15, 86.7%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_compound_buyers_fresh_age err: {_e}")

            # ── trigger_3d_strong_h1_fresh_daytime — n=15, 86.7% WR ──
            # bs_h1[1.3,1.6) × age[6,24h) × hour_ct[12,17).
            _trigger_3d_strong_h1_fresh_daytime_match = False
            _trigger_3d_strong_h1_fresh_daytime_reasons: list = []
            try:
                _fd_e_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                _fd_e_age = entry_age_hours if "entry_age_hours" in dir() else None
                if _fd_e_age is None:
                    _fd_e_age = (_tier3_features or {}).get("hours_since_graduation")
                if (
                    12 <= _ovn_h < 17
                    and _fd_e_bsh1 is not None and 1.3 <= _fd_e_bsh1 < 1.6
                    and isinstance(_fd_e_age, (int, float)) and 6 <= _fd_e_age < 24
                ):
                    _trigger_3d_strong_h1_fresh_daytime_match = True
                    _trigger_3d_strong_h1_fresh_daytime_reasons.append(
                        f"hour={_ovn_h}∈[12,17) AND bs_h1={_fd_e_bsh1:.2f} "
                        f"AND age={_fd_e_age:.0f}h (3D n=15, 86.7%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_strong_h1_fresh_daytime err: {_e}")

            # ── trigger_3d_midrange_midcap_predawn — n=17, 88.2% WR ──
            # ratio[0.5,0.7) × mcap[$2M,$10M) × hour_ct[4,8).
            _trigger_3d_midrange_midcap_predawn_match = False
            _trigger_3d_midrange_midcap_predawn_reasons: list = []
            try:
                _fd_f_ratio = (_lifecycle_dict or {}).get("lifecycle_h24_ratio")
                _fd_f_mc = float(mcap) if mcap is not None else None
                if (
                    4 <= _ovn_h < 8
                    and isinstance(_fd_f_ratio, (int, float)) and 0.5 <= _fd_f_ratio < 0.7
                    and _fd_f_mc is not None and 2_000_000 <= _fd_f_mc < 10_000_000
                ):
                    _trigger_3d_midrange_midcap_predawn_match = True
                    _trigger_3d_midrange_midcap_predawn_reasons.append(
                        f"hour={_ovn_h}∈[4,8) AND ratio={_fd_f_ratio:.2f} "
                        f"AND mcap=${_fd_f_mc/1e6:.1f}M (3D n=17, 88.2%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_midrange_midcap_predawn err: {_e}")

            # ── trigger_3d_bigpump_midcap_24_7 — n=28, 89.3% WR ──
            # bs_h6[1.1,1.3) × peak[1000+) × mcap[$2M,$10M). 24/7 — broader
            # version of overnight AZ trigger (which has n=9 100% overnight).
            _trigger_3d_bigpump_midcap_24_7_match = False
            _trigger_3d_bigpump_midcap_24_7_reasons: list = []
            try:
                _fd_g_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _fd_g_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _fd_g_mc = float(mcap) if mcap is not None else None
                if (
                    _fd_g_bsh6 is not None and 1.1 <= _fd_g_bsh6 < 1.3
                    and _fd_g_peak is not None and _fd_g_peak >= 1000
                    and _fd_g_mc is not None and 2_000_000 <= _fd_g_mc < 10_000_000
                ):
                    _trigger_3d_bigpump_midcap_24_7_match = True
                    _trigger_3d_bigpump_midcap_24_7_reasons.append(
                        f"bs_h6={_fd_g_bsh6:.2f} AND peak={_fd_g_peak:.0f}%>=1000 "
                        f"AND mcap=${_fd_g_mc/1e6:.1f}M (3D 24/7 n=28, 89.3%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_bigpump_midcap_24_7 err: {_e}")

            # ── trigger_3d_compound_midcap_fresh_age — n=28, 89.3% WR ──
            # bs_h6[1.1,1.3) × mcap[$2M,$10M) × age[6,24h). 24/7.
            _trigger_3d_compound_midcap_fresh_age_match = False
            _trigger_3d_compound_midcap_fresh_age_reasons: list = []
            try:
                _fd_h_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _fd_h_mc = float(mcap) if mcap is not None else None
                _fd_h_age = entry_age_hours if "entry_age_hours" in dir() else None
                if _fd_h_age is None:
                    _fd_h_age = (_tier3_features or {}).get("hours_since_graduation")
                if (
                    _fd_h_bsh6 is not None and 1.1 <= _fd_h_bsh6 < 1.3
                    and _fd_h_mc is not None and 2_000_000 <= _fd_h_mc < 10_000_000
                    and isinstance(_fd_h_age, (int, float)) and 6 <= _fd_h_age < 24
                ):
                    _trigger_3d_compound_midcap_fresh_age_match = True
                    _trigger_3d_compound_midcap_fresh_age_reasons.append(
                        f"bs_h6={_fd_h_bsh6:.2f} AND mcap=${_fd_h_mc/1e6:.1f}M "
                        f"AND age={_fd_h_age:.0f}h (3D 24/7 n=28, 89.3%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_compound_midcap_fresh_age err: {_e}")

            # ── trigger_3d_extreme_h1_midliq_predawn — n=10, 90% WR ──
            # bs_h1[2,99) × liq[$250k,$1M) × hour_ct[4,8).
            _trigger_3d_extreme_h1_midliq_predawn_match = False
            _trigger_3d_extreme_h1_midliq_predawn_reasons: list = []
            try:
                _fd_i_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                _fd_i_liq = float(liquidity_usd) if "liquidity_usd" in dir() and liquidity_usd is not None else None
                if _fd_i_liq is None:
                    _fd_i_liq = float((pair.get("liquidity") or {}).get("usd") or 0) if "pair" in dir() else None
                if (
                    4 <= _ovn_h < 8
                    and _fd_i_bsh1 is not None and _fd_i_bsh1 >= 2.0
                    and _fd_i_liq is not None and 250_000 <= _fd_i_liq < 1_000_000
                ):
                    _trigger_3d_extreme_h1_midliq_predawn_match = True
                    _trigger_3d_extreme_h1_midliq_predawn_reasons.append(
                        f"hour={_ovn_h}∈[4,8) AND bs_h1={_fd_i_bsh1:.2f}>=2 "
                        f"AND liq=${_fd_i_liq/1000:.0f}k (3D n=10, 90.0%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_extreme_h1_midliq_predawn err: {_e}")

            # ── trigger_3d_compound_strong5m_midtrade — n=10, 100% WR ──
            # bs_h6[1.1,1.3) × bs_m5[1.5,2) × avg_trade[$200,$500). 24/7.
            _trigger_3d_compound_strong5m_midtrade_match = False
            _trigger_3d_compound_strong5m_midtrade_reasons: list = []
            try:
                _fd_j_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _fd_j_bsm5 = float(ratio_m5) if ratio_m5 not in (None, float("inf")) else None
                _fd_j_ats = float(avg_trade_size_h1) if avg_trade_size_h1 is not None else None
                if (
                    _fd_j_bsh6 is not None and 1.1 <= _fd_j_bsh6 < 1.3
                    and _fd_j_bsm5 is not None and 1.5 <= _fd_j_bsm5 < 2.0
                    and _fd_j_ats is not None and 200 <= _fd_j_ats < 500
                ):
                    _trigger_3d_compound_strong5m_midtrade_match = True
                    _trigger_3d_compound_strong5m_midtrade_reasons.append(
                        f"bs_h6={_fd_j_bsh6:.2f} AND bs_m5={_fd_j_bsm5:.2f} "
                        f"AND avg_trade=${_fd_j_ats:.0f} (3D 24/7 n=10, 100%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_compound_strong5m_midtrade err: {_e}")

            # ── trigger_3d_mature_midcap_postmidnight — n=13, 92.3% WR ──
            # cycles[60,150) × mcap[$2M,$10M) × hour_ct[0,4).
            _trigger_3d_mature_midcap_postmidnight_match = False
            _trigger_3d_mature_midcap_postmidnight_reasons: list = []
            try:
                _fd_k_cyc = cycles_seen
                _fd_k_mc = float(mcap) if mcap is not None else None
                if (
                    0 <= _ovn_h < 4
                    and isinstance(_fd_k_cyc, (int, float)) and 60 <= _fd_k_cyc < 150
                    and _fd_k_mc is not None and 2_000_000 <= _fd_k_mc < 10_000_000
                ):
                    _trigger_3d_mature_midcap_postmidnight_match = True
                    _trigger_3d_mature_midcap_postmidnight_reasons.append(
                        f"hour={_ovn_h}∈[0,4) AND cycles={int(_fd_k_cyc)} "
                        f"AND mcap=${_fd_k_mc/1e6:.1f}M (3D n=13, 92.3%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_mature_midcap_postmidnight err: {_e}")

            # ═══ DEEP-MINING 3D TRIGGERS (WR>=80%) — ENFORCED 2026-05-15 ═══════
            # From mine_deep_multi_dim.py — 148,706 cells scanned. All below
            # hit WR>=80% AND n>=20 (high precision + medium-to-high volume).
            # Several use compound feature bs_h6 × bs_h1 (the product) which
            # surfaced as a stronger signal than either alone.

            # Pre-compute compound feature once for reuse below
            _bs_h6_h1_product = None
            try:
                if (bs_h6 not in (None, float("inf"))
                        and bs_h1 not in (None, float("inf"))):
                    _bs_h6_h1_product = float(bs_h6) * float(bs_h1)
            except Exception:
                pass
            # drop_from_peak = (1 - h24_ratio_to_peak) * peak — abs drop magnitude
            _drop_from_peak = None
            try:
                _dfp_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _dfp_ratio = (_lifecycle_dict or {}).get("lifecycle_h24_ratio")
                if (_dfp_peak is not None
                        and isinstance(_dfp_ratio, (int, float))):
                    _drop_from_peak = (1.0 - _dfp_ratio) * _dfp_peak
            except Exception:
                pass

            # ── trigger_3d_liq_midcap_compound — n=53, 83.0% WR ──
            # liq[$100k,$250k) × mcap[$2M,$10M) × bs_h6×h1[1.3,1.8)
            _trigger_3d_liq_midcap_compound_match = False
            _trigger_3d_liq_midcap_compound_reasons: list = []
            try:
                _bo_liq = float(liquidity_usd) if "liquidity_usd" in dir() and liquidity_usd is not None else None
                if _bo_liq is None:
                    _bo_liq = float((pair.get("liquidity") or {}).get("usd") or 0) if "pair" in dir() else None
                _bo_mc = float(mcap) if mcap is not None else None
                if (
                    _bo_liq is not None and 100_000 <= _bo_liq < 250_000
                    and _bo_mc is not None and 2_000_000 <= _bo_mc < 10_000_000
                    and _bs_h6_h1_product is not None and 1.3 <= _bs_h6_h1_product < 1.8
                ):
                    _trigger_3d_liq_midcap_compound_match = True
                    _trigger_3d_liq_midcap_compound_reasons.append(
                        f"liq=${_bo_liq/1000:.0f}k AND mcap=${_bo_mc/1e6:.1f}M "
                        f"AND bs_h6×h1={_bs_h6_h1_product:.2f} (3D n=53, 83.0%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_liq_midcap_compound err: {_e}")

            # ── trigger_3d_h6_fresh_age_compound — n=65, 81.5% WR ──
            # bs_h6[1.1,1.3) × age[6,24h) × bs_h6×h1[1.3,1.8)
            _trigger_3d_h6_fresh_age_compound_match = False
            _trigger_3d_h6_fresh_age_compound_reasons: list = []
            try:
                _bp_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _bp_age = entry_age_hours if "entry_age_hours" in dir() else None
                if _bp_age is None:
                    _bp_age = (_tier3_features or {}).get("hours_since_graduation")
                if (
                    _bp_bsh6 is not None and 1.1 <= _bp_bsh6 < 1.3
                    and isinstance(_bp_age, (int, float)) and 6 <= _bp_age < 24
                    and _bs_h6_h1_product is not None and 1.3 <= _bs_h6_h1_product < 1.8
                ):
                    _trigger_3d_h6_fresh_age_compound_match = True
                    _trigger_3d_h6_fresh_age_compound_reasons.append(
                        f"bs_h6={_bp_bsh6:.2f} AND age={_bp_age:.0f}h "
                        f"AND bs_h6×h1={_bs_h6_h1_product:.2f} (3D n=65, 81.5%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_h6_fresh_age_compound err: {_e}")

            # ── trigger_3d_h1_midcap_liq_24_7 — n=51, 80.4% WR ──
            # bs_h1[1.1,1.3) × liq[$100k,$250k) × mcap[$2M,$10M)
            # (24/7 version of BA overnight-gated trigger)
            _trigger_3d_h1_midcap_liq_24_7_match = False
            _trigger_3d_h1_midcap_liq_24_7_reasons: list = []
            try:
                _bq_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                _bq_liq = float(liquidity_usd) if "liquidity_usd" in dir() and liquidity_usd is not None else None
                if _bq_liq is None:
                    _bq_liq = float((pair.get("liquidity") or {}).get("usd") or 0) if "pair" in dir() else None
                _bq_mc = float(mcap) if mcap is not None else None
                if (
                    _bq_bsh1 is not None and 1.1 <= _bq_bsh1 < 1.3
                    and _bq_liq is not None and 100_000 <= _bq_liq < 250_000
                    and _bq_mc is not None and 2_000_000 <= _bq_mc < 10_000_000
                ):
                    _trigger_3d_h1_midcap_liq_24_7_match = True
                    _trigger_3d_h1_midcap_liq_24_7_reasons.append(
                        f"bs_h1={_bq_bsh1:.2f} AND liq=${_bq_liq/1000:.0f}k "
                        f"AND mcap=${_bq_mc/1e6:.1f}M (3D 24/7 n=51, 80.4%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_h1_midcap_liq_24_7 err: {_e}")

            # ── trigger_3d_h6_smallpump_midtrade — n=20, 80.0% WR ──
            # bs_h6[1.1,1.3) × peak[25,50) × avg_trade[$200,$500)
            _trigger_3d_h6_smallpump_midtrade_match = False
            _trigger_3d_h6_smallpump_midtrade_reasons: list = []
            try:
                _br_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _br_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _br_ats = float(avg_trade_size_h1) if avg_trade_size_h1 is not None else None
                if (
                    _br_bsh6 is not None and 1.1 <= _br_bsh6 < 1.3
                    and _br_peak is not None and 25 <= _br_peak < 50
                    and _br_ats is not None and 200 <= _br_ats < 500
                ):
                    _trigger_3d_h6_smallpump_midtrade_match = True
                    _trigger_3d_h6_smallpump_midtrade_reasons.append(
                        f"bs_h6={_br_bsh6:.2f} AND peak={_br_peak:.0f}% "
                        f"AND avg_trade=${_br_ats:.0f} (3D n=20, 80.0%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_h6_smallpump_midtrade err: {_e}")

            # ── trigger_3d_h6_strong5m_old — n=21, 81.0% WR ──
            # bs_h6[1.1,1.3) × bs_m5[1.5,2) × age[720h+) (~30d+)
            _trigger_3d_h6_strong5m_old_match = False
            _trigger_3d_h6_strong5m_old_reasons: list = []
            try:
                _bs_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _bs_bsm5 = float(ratio_m5) if ratio_m5 not in (None, float("inf")) else None
                _bs_age = entry_age_hours if "entry_age_hours" in dir() else None
                if _bs_age is None:
                    _bs_age = (_tier3_features or {}).get("hours_since_graduation")
                if (
                    _bs_bsh6 is not None and 1.1 <= _bs_bsh6 < 1.3
                    and _bs_bsm5 is not None and 1.5 <= _bs_bsm5 < 2.0
                    and isinstance(_bs_age, (int, float)) and _bs_age >= 720
                ):
                    _trigger_3d_h6_strong5m_old_match = True
                    _trigger_3d_h6_strong5m_old_reasons.append(
                        f"bs_h6={_bs_bsh6:.2f} AND bs_m5={_bs_bsm5:.2f} "
                        f"AND age={_bs_age:.0f}h>=720h (3D n=21, 81.0%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_h6_strong5m_old err: {_e}")

            # ── trigger_3d_h6_midcap_deepdrop — n=23, 95.7% WR ──
            # bs_h6[1.1,1.3) × mcap[$2M,$10M) × drop_from_peak[1000+%)
            _trigger_3d_h6_midcap_deepdrop_match = False
            _trigger_3d_h6_midcap_deepdrop_reasons: list = []
            try:
                _bt_bsh6 = float(bs_h6) if bs_h6 not in (None, float("inf")) else None
                _bt_mc = float(mcap) if mcap is not None else None
                if (
                    _bt_bsh6 is not None and 1.1 <= _bt_bsh6 < 1.3
                    and _bt_mc is not None and 2_000_000 <= _bt_mc < 10_000_000
                    and _drop_from_peak is not None and _drop_from_peak >= 1000
                ):
                    _trigger_3d_h6_midcap_deepdrop_match = True
                    _trigger_3d_h6_midcap_deepdrop_reasons.append(
                        f"bs_h6={_bt_bsh6:.2f} AND mcap=${_bt_mc/1e6:.1f}M "
                        f"AND drop_from_peak={_drop_from_peak:.0f}%>=1000 "
                        f"(3D n=23, 95.7%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_h6_midcap_deepdrop err: {_e}")

            # ── trigger_3d_bigpump_midcap_compound — n=27, 96.3% WR ──
            # peak[1000+) × mcap[$2M,$10M) × bs_h6×h1[1.3,1.8)
            _trigger_3d_bigpump_midcap_compound_match = False
            _trigger_3d_bigpump_midcap_compound_reasons: list = []
            try:
                _bu_peak = float(peak_h24_6h) if peak_h24_6h is not None else None
                _bu_mc = float(mcap) if mcap is not None else None
                if (
                    _bu_peak is not None and _bu_peak >= 1000
                    and _bu_mc is not None and 2_000_000 <= _bu_mc < 10_000_000
                    and _bs_h6_h1_product is not None and 1.3 <= _bs_h6_h1_product < 1.8
                ):
                    _trigger_3d_bigpump_midcap_compound_match = True
                    _trigger_3d_bigpump_midcap_compound_reasons.append(
                        f"peak={_bu_peak:.0f}%>=1000 AND mcap=${_bu_mc/1e6:.1f}M "
                        f"AND bs_h6×h1={_bs_h6_h1_product:.2f} (3D n=27, 96.3%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_bigpump_midcap_compound err: {_e}")

            # ── trigger_3d_midcap_fresh_age_compound — n=27, 96.3% WR ──
            # mcap[$2M,$10M) × age[6,24h) × bs_h6×h1[1.3,1.8)
            _trigger_3d_midcap_fresh_age_compound_match = False
            _trigger_3d_midcap_fresh_age_compound_reasons: list = []
            try:
                _bv_mc = float(mcap) if mcap is not None else None
                _bv_age = entry_age_hours if "entry_age_hours" in dir() else None
                if _bv_age is None:
                    _bv_age = (_tier3_features or {}).get("hours_since_graduation")
                if (
                    _bv_mc is not None and 2_000_000 <= _bv_mc < 10_000_000
                    and isinstance(_bv_age, (int, float)) and 6 <= _bv_age < 24
                    and _bs_h6_h1_product is not None and 1.3 <= _bs_h6_h1_product < 1.8
                ):
                    _trigger_3d_midcap_fresh_age_compound_match = True
                    _trigger_3d_midcap_fresh_age_compound_reasons.append(
                        f"mcap=${_bv_mc/1e6:.1f}M AND age={_bv_age:.0f}h "
                        f"AND bs_h6×h1={_bs_h6_h1_product:.2f} (3D n=27, 96.3%WR)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 3d_midcap_fresh_age_compound err: {_e}")

            # ── trigger_overnight_mature_midcap — ENFORCED 2026-05-14 PM ────
            # Rank-10 from mining: cycles_seen in [60,150) AND
            # entry_market_cap_usd in [$2M, $10M). Overnight n=50, 66.0%
            # WR, +$108 vs daytime 47.8% WR, -$83 (Δ +18.2pp, daytime is
            # net LOSER on the same cohort).
            _trigger_overnight_mature_midcap_match = False
            _trigger_overnight_mature_midcap_reasons: list = []
            try:
                _omm_cyc = cycles_seen
                _omm_mc = float(mcap) if mcap is not None else None
                if (
                    _ovn_active
                    and isinstance(_omm_cyc, (int, float))
                    and 60 <= _omm_cyc < 150
                    and _omm_mc is not None and 2_000_000 <= _omm_mc < 10_000_000
                ):
                    _trigger_overnight_mature_midcap_match = True
                    _trigger_overnight_mature_midcap_reasons.append(
                        f"hour_ct={_ovn_h} in [19,7) AND cycles={int(_omm_cyc)} "
                        f"AND mcap=${_omm_mc/1e6:.1f}M (overnight n=50, 66.0%WR, Δ+18.2pp)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] overnight_mature_midcap err: {_e}")

            # ── trigger_chart_channel_strong — ENFORCED 2026-05-16 PM ──────
            # New entry path mined from our own closed trades. Feature
            # chart_trendline_5m_channel_pos (percent position within 5m
            # price channel) is the single strongest winner discriminator
            # we found on our actual trade data — Cohen's d=+1.46 (winners
            # avg 33, losers avg 4).
            #
            # Validation on our 87 7d trades (24 had coverage):
            #   channel_pos >= 15: n=9, 78% WR, total +$2.79
            #   channel_pos >= 20: n=6, 100% WR, total +$3.07
            #   channel_pos <  10: n=14, 36% WR, total -$5.86
            #
            # Mechanism: high channel_pos means price is at the TOP of its
            # 5m channel — momentum is up, not down. Counter to "dip-buy"
            # intuition but consistent with "buy strength" pattern.
            # Sparse coverage (~30% of trades) — fail-open if missing.
            # Standard tier sizing — strong enough signal.
            _trigger_chart_channel_strong_match = False
            _trigger_chart_channel_strong_reasons: list = []
            try:
                _cp_val = (_chart_ctx_dict or {}).get("chart_trendline_5m_channel_pos")
                if isinstance(_cp_val, (int, float)) and float(_cp_val) >= 15.0:
                    _trigger_chart_channel_strong_match = True
                    _trigger_chart_channel_strong_reasons.append(
                        f"chart_trendline_5m_channel_pos={float(_cp_val):.1f}>=15 "
                        f"(78% WR on n=9 in 7d trades, d=+1.46 winner separator)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] chart_channel_strong trigger err: {_e}")

            # ── trigger_late_night_fresh — ENFORCED 2026-05-16 PM ──────────
            # Tightened 2026-05-16 PM after deep-dip subset analysis: adding
            # pc_m5 < -10 condition flips avg_exit from -2.6% to +0.4% —
            # the ONLY universe-mined compound with POSITIVE avg realized
            # outcome (not just peak).
            #
            # Universe-recorder validation (n=127 in 24h, gate-passing):
            #   won_10pct=77%  (peak >= +10%)
            #   safe_win=50%   (peak >= +10% AND exit >= -5%)
            #   big_safe=42%   (peak >= +20% AND exit >= 0)
            #   avg peak +46.6%, avg exit +0.4%
            #
            # Mechanism: the deep m5 dip (>-10%) on a fresh post-launch
            # token during late-night CT is the "post-pump panic flush
            # before continuation" pattern. Without the dip qualifier we
            # also catch peak-and-distribute tokens (those crash exit).
            #
            # Reference missed winners (CT22-2 + age<6h + pc_m5<-10):
            #   Twerk  hr= 0 age=2.9h pc_m5=-26% peak +226% exit +205%
            #   Jim    hr=22 age=0.2h pc_m5=-21% peak +220% exit +182%
            #   GANG   hr=23 age=0.9h pc_m5=-15% peak +204% exit +179%
            #   WANTED hr=22 age=1.2h pc_m5=-30% peak +137% exit  +55%
            #
            # Predicate: hour_ct in {22,23,0,1,2} AND age<6h AND pc_m5<-10
            # Standard sizing — strong signal, NOT marginal.
            _trigger_late_night_fresh_match = False
            _trigger_late_night_fresh_reasons: list = []
            try:
                from datetime import datetime, timezone, timedelta
                _now_utc = datetime.now(timezone.utc)
                # CT = UTC - 5 (DST May 2026)
                _hour_ct = (_now_utc - timedelta(hours=5)).hour
                if (_hour_ct in {22, 23, 0, 1, 2}
                        and pair_age_hours is not None and pair_age_hours < 6.0
                        and pc_m5 is not None and float(pc_m5) < -10.0):
                    _trigger_late_night_fresh_match = True
                    _trigger_late_night_fresh_reasons.append(
                        f"hour_ct={_hour_ct} in [22-02] AND age={pair_age_hours:.1f}h<6 "
                        f"AND pc_m5={float(pc_m5):.1f}%<-10 "
                        f"(77% won_10pct, +0.4% avg_exit on n=127 in 24h)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] late_night_fresh trigger err: {_e}")

            # ── trigger_fresh_pump_retrace — ENFORCED 2026-05-16 PM ─────────
            # New entry path mined from universe-recorder data (838 broader-
            # universe dip events / ~24h). Pattern catches fresh-graduate
            # post-pump m5 retrace tokens our existing triggers don't fire on
            # (they need accumulated chart history that doesn't exist for
            # <1h old tokens).
            #
            # Compound predicate:
            #   age_hours <= 30      — fresh-graduate / post-pump cohort
            #   sells_h1 >= 1500     — high trade activity (token is liquid)
            #   pc_m5    <= -10      — sharp 5m retrace (deep dip)
            #
            # Universe-recorder validation: matches 119 of 838 events at
            # 66% won_20pct rate (vs 30% baseline), avg peak +45%.
            # Of 21 unique gate-passing missed big-winners in 24h, this
            # compound catches 9 (e.g. Yae +124%, WANTED +106%, Twerk +77%).
            #
            # SIZING NOTE: marked as MARGINAL for position sizing tier
            # (gets $10 base size when solo, scales up if compound) until
            # we accumulate forward live P&L data. Real exit slip on
            # microcap liq ($30-60k) is unproven.
            _trigger_fresh_pump_retrace_match = False
            _trigger_fresh_pump_retrace_reasons: list = []
            try:
                _fpr_age = pair_age_hours
                _fpr_sells = pair.get("txns", {}).get("h1", {}).get("sells", 0) if isinstance(pair.get("txns"), dict) else 0
                _fpr_pcm5 = pc_m5
                if (_fpr_age is not None and _fpr_age <= 30.0
                    and _fpr_sells is not None and float(_fpr_sells) >= 1500
                    and _fpr_pcm5 is not None and float(_fpr_pcm5) <= -10.0):
                    _trigger_fresh_pump_retrace_match = True
                    _trigger_fresh_pump_retrace_reasons.append(
                        f"age={_fpr_age:.1f}h<=30 AND sells_h1={int(_fpr_sells)}>=1500 "
                        f"AND pc_m5={_fpr_pcm5:.1f}%<=-10 (fresh-pump m5 retrace)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] fresh_pump_retrace trigger err: {_e}")

            # ── trigger_whale_conviction — ENFORCED 2026-05-14 PM (Commit C) ─
            # Positive entry trigger from 35-angle deep mining. Fires on:
            #   - whale_buy_present_2k == True (a $2k+ whale buy in lookback)
            #   - OR top10_buyer_within_60s_count >= 3 (3+ top-10 buyers
            #     clustered within 60s of signal)
            # Held-out mining results (recent 7d):
            #   whale_buy_present_2k=True: n=20, 70% WR, +$0.81/tr
            #   top10_buyer_within_60s>=3: n=21, 71% WR, +$0.52/tr
            # Mechanism: both signals indicate a real, conviction-level
            # buyer recently entered — pre-cursor to broader interest.
            # (Note: top1_share_of_top10>=0.70 is the 3rd component but it's
            # computed in trader.py post-rugcheck, can't be used here.)
            _trigger_whale_conviction_match = False
            _trigger_whale_conviction_reasons: list = []
            try:
                _wc_whale = (_trade_log_dict or {}).get("whale_buy_present_2k")
                _wc_t10_60s = (_tier2_features or {}).get("top10_buyer_within_60s_count")
                if _wc_whale is True:
                    _trigger_whale_conviction_match = True
                    _trigger_whale_conviction_reasons.append(
                        "whale_buy_present_2k=True (recent $2k+ whale buy)"
                    )
                if _wc_t10_60s is not None and float(_wc_t10_60s) >= 3.0:
                    _trigger_whale_conviction_match = True
                    _trigger_whale_conviction_reasons.append(
                        f"top10_buyer_within_60s_count={int(float(_wc_t10_60s))}>=3 "
                        f"(clustered top-10 buyers)"
                    )
                # GATE 2026-05-14 PM: skip the [0.80, 0.95) h24_ratio_to_peak
                # dead zone — the "approaching peak after pump" pattern.
                # Mining (top10>=3 branch, n=155): ratio 0.80-0.95 had only
                # 39.4% WR / -$6.44 across 33 fires vs 54.8% baseline.
                # Live confirmation: RAGEGUY 17:14 entry (ratio in this band)
                # bought a stalled retracement-recovery, never went green,
                # dumped -4.9%. Inverse band (peak>=200% AND ratio<0.40)
                # showed 75% WR +$3.95 — the carve-out is precision-focused,
                # preserves the strong "deep retracement after pump" pattern.
                _wc_ratio = (_lifecycle_dict or {}).get("lifecycle_h24_ratio")
                if (
                    _trigger_whale_conviction_match
                    and isinstance(_wc_ratio, (int, float))
                    and 0.80 <= _wc_ratio < 0.95
                ):
                    _trigger_whale_conviction_match = False
                    _trigger_whale_conviction_reasons.append(
                        f"GATED: h24_ratio_to_peak={_wc_ratio:.2f} in dead zone "
                        f"[0.80, 0.95) — mid-retracement-recovery, see audit"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] whale_conviction trigger err: {_e}")

            # ── trigger_cascade_v_bottom — SHADOW 2026-05-14 PM ─────────────
            # Catches V-bottoms after a violent 1m cascade. Ground-truth from
            # BURNIE 2026-05-14 15:53:18 CT: after the 15:50 -5.12% cascade
            # ($8k vol, 1m_vs ~5x), 1s bars at 15:53:18 had cum_30s=+1.20%,
            # close_pos_60s=0.99, vol_burst_30s=2.6x. Recovery: +2.4% in 33s,
            # peak +7.3% within 30s. Same morphology observed at 04:10,
            # 05:25, 07:55 CT this morning (5m -5 to -8% drops then
            # +6-11% V-recoveries) but DexScreener API doesn't expose 1s
            # data >2h old so threshold is set conservatively (n=1 confirmed).
            #
            # Predicate (compound):
            #   1m_cum_3min_pct <= -3.0  (recent dramatic 1m dump)
            #   AND 1m_volume_spike >= 3.0  (real volume cascade — not slow bleed)
            #   AND 1s_close_pos_60s >= 0.85  (close in top 15% of 60s range)
            #   AND 1s_vol_burst_on_reversal_ratio >= 1.5  (vol returning post-bottom)
            #
            # SHADOW MODE: logs match but does NOT contribute to _triggers_fired
            # yet. Forward-validate 24-48h before promoting to ENFORCED. The
            # signal_event_recorder will capture every shadow-match for audit.
            _trigger_cascade_v_bottom_match = False
            _trigger_cascade_v_bottom_reasons: list = []
            try:
                _cvb_m1c = m1_features.get("1m_cum_3min_pct")
                _cvb_m1v = m1_features.get("1m_volume_spike")
                _cvb_cpos = _1s_features.get("close_pos_60s") if isinstance(_1s_features, dict) else None
                _cvb_vbst = _1s_features.get("vol_burst_on_reversal_ratio") if isinstance(_1s_features, dict) else None
                if (_cvb_m1c is not None and _cvb_m1c <= -3.0
                        and _cvb_m1v is not None and _cvb_m1v >= 3.0
                        and _cvb_cpos is not None and float(_cvb_cpos) >= 0.85
                        and _cvb_vbst is not None and float(_cvb_vbst) >= 1.5):
                    _trigger_cascade_v_bottom_match = True
                    _trigger_cascade_v_bottom_reasons.append(
                        f"1m_cum3={_cvb_m1c:.2f}<=-3.0 AND 1m_vs={_cvb_m1v:.2f}>=3.0 "
                        f"AND 1s_cpos={float(_cvb_cpos):.2f}>=0.85 "
                        f"AND vbst={float(_cvb_vbst):.2f}>=1.5"
                    )
                    logger.info(
                        f"[DipScanner] cascade_v_bottom SHADOW MATCH: {token_symbol} "
                        f"{_trigger_cascade_v_bottom_reasons[0]}"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] cascade_v_bottom err: {_e}")

            # ── trigger_mcap_psych_level — ENFORCED 2026-05-13 PM ────────────
            # Round-7: mcap_near_psych_level == True → 5W/0L.
            # Predicate: token mcap within 5% of $1M/$2M/$5M/$10M/$25M/$50M/$100M.
            # Computed by feeds.lifecycle_stage.mcap_magnetism — already in entry_meta.
            _trigger_mcap_psych_match = False
            _trigger_mcap_psych_reasons: list = []
            try:
                _mp = _lifecycle_dict.get("mcap_near_psych_level") if isinstance(_lifecycle_dict, dict) else None
                if _mp is True:
                    _trigger_mcap_psych_match = True
                    _mp_lvl = _lifecycle_dict.get("mcap_nearest_psych_level_usd") or 0
                    _mp_dist = _lifecycle_dict.get("mcap_distance_to_psych_pct") or 0
                    _trigger_mcap_psych_reasons.append(
                        f"mcap_near_psych_level=True (lvl=${_mp_lvl:.0f}, "
                        f"dist={_mp_dist:.1f}%)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] mcap_psych trigger err: {_e}")

            # ── filter_mtf_strong_downtrend — ENFORCED 2026-05-13 PM ─────────
            # Round-7: chart_mtf_score <= -2 → 0W/5L. Strong multi-tf
            # downtrend = block. Even if some trigger fires, this hard filter
            # overrides because mtf<=-2 means EVERY higher TF is bearish.
            _filter_mtf_dn_block_reasons: list = []
            try:
                _mtf_score = None
                try:
                    _mtf_score = _chart_ctx.mtf.get("score") if _chart_ctx else None
                except Exception:
                    _mtf_score = None
                if _mtf_score is not None and float(_mtf_score) <= -2.0:
                    _filter_mtf_dn_block_reasons.append(
                        f"chart_mtf_score={float(_mtf_score):.1f}<=-2.0 "
                        f"(strong multi-tf downtrend)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] mtf_downtrend filter err: {_e}")
            # CARVE-OUT 2026-05-16 PM: rescue when chart_score >= 58.20.
            # Mining: mtf_strong_downtrend-blocked + chart_score>=58 → n=54,
            # 65% won_10pct, **+30.8% avg peak** (highest peak of any carve-out
            # found in this round). Strong chart pattern overrides MTF bear
            # bias — these are the rare reversal candidates that look bad
            # on higher TFs but have a strong underlying setup (V-bottom,
            # accumulation, breakout pattern).
            _mtf_dn_chart_carve = False
            try:
                _mtf_carve_chart_score = (_chart_ctx_dict or {}).get("chart_score")
                if (_filter_mtf_dn_block_reasons
                    and isinstance(_mtf_carve_chart_score, (int, float))
                    and float(_mtf_carve_chart_score) >= 58.20):
                    _mtf_dn_chart_carve = True
            except Exception:
                pass
            # CARVE-OUT 2026-05-16 PM #2: calm_seller signature.
            # Universe mining (n=2049): sells_h1 <= 411 AND mcap >= $531k
            # has 100% loose-WR (peak >= +5 AND exit >= -5). The "calm
            # seller + moderate mcap" cohort consistently produces controlled
            # accumulation moves. mtf_strong_downtrend is blocking 399 of
            # these per 4.5h window (40% of all calm-micro-cap blocks).
            #
            # Predicate: sells_h1 <= 411 AND mcap >= $531,083.
            # Caveat: universe-to-live divergence unresolved (live calm_hot
            # 17% WR vs universe 58% — see investigate_universe_gap.py).
            # Carve-out is narrow (sells_h1 <= 411 is the strictest cut),
            # so volume add should be bounded.
            _mtf_dn_calm_seller_carve = False
            try:
                if (_filter_mtf_dn_block_reasons
                    and isinstance(s_h1, (int, float)) and s_h1 <= 411
                    and isinstance(mcap, (int, float)) and mcap >= 531_083):
                    _mtf_dn_calm_seller_carve = True
            except Exception:
                pass
            # CARVE-OUT 2026-05-16 PM #3: shallow-1h-dip signature.
            # Realistic-P&L sim of mtf_strong_downtrend blocks (mtf_audit_v2,
            # n=34 audited): sim winners had pc_h1 mean -15.6%, sim losers
            # had pc_h1 mean -24.4%. Cohen's d=+0.65 — strongest single
            # discriminator within the blocked cohort.
            #
            # When mtf_strong_downtrend fires but pc_h1 > -20%, the dip is
            # moderate and recovers more often than not under our exit
            # logic (TP1 +3%/50%, trail 1pp, stop -4%). When pc_h1 < -20%,
            # it's a real falling knife — keep blocking.
            #
            # Est EV: +$0.20/blocked-trade × ~5 entries/day = +$1/day.
            # Small but positive; preserves block on deep-knife cohort.
            _mtf_dn_pc_h1_carve = False
            try:
                if (_filter_mtf_dn_block_reasons
                    and isinstance(pc_h1, (int, float)) and pc_h1 > -20.0):
                    _mtf_dn_pc_h1_carve = True
            except Exception:
                pass
            # CARVE-OUT 2026-05-16 PM #4: breakthrough-early flag.
            # When the EARLY-flag of strong_orderflow or sustained_
            # accumulation already matches at this point, the candidate
            # is in the 72-100% WR cohort regardless of mtf state.
            _mtf_dn_breakthrough_carve = False
            if (_filter_mtf_dn_block_reasons
                and not (_mtf_dn_chart_carve or _mtf_dn_calm_seller_carve or _mtf_dn_pc_h1_carve)
                and _breakthrough_early_match):
                _mtf_dn_breakthrough_carve = True
            _filter_mtf_dn_verdict = (
                "BLOCK" if (_filter_mtf_dn_block_reasons
                            and not _mtf_dn_chart_carve
                            and not _mtf_dn_calm_seller_carve
                            and not _mtf_dn_pc_h1_carve
                            and not _mtf_dn_breakthrough_carve)
                else "PASS"
            )
            c[f"filter_mtf_strong_downtrend_{_filter_mtf_dn_verdict.lower()}"] = c.get(
                f"filter_mtf_strong_downtrend_{_filter_mtf_dn_verdict.lower()}", 0
            ) + 1
            if _filter_mtf_dn_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_mtf_strong_downtrend: "
                    f"{token_symbol} reasons={','.join(_filter_mtf_dn_block_reasons)}"
                )
                continue
            if _mtf_dn_chart_carve and _filter_mtf_dn_block_reasons:
                logger.info(
                    f"[DipScanner] filter_mtf_strong_downtrend RESCUED by chart_score: "
                    f"{token_symbol} chart_score={(_chart_ctx_dict or {}).get('chart_score', 0):.1f}>=58.2 "
                    f"(65% won_10pct on n=54 in 4d mining, +30.8% avg peak)"
                )
                c["filter_mtf_dn_carve_chart_score"] = c.get("filter_mtf_dn_carve_chart_score", 0) + 1
            if _mtf_dn_calm_seller_carve and _filter_mtf_dn_block_reasons:
                logger.info(
                    f"[DipScanner] filter_mtf_strong_downtrend RESCUED by calm_seller: "
                    f"{token_symbol} sells_h1={s_h1} mcap=${mcap:,.0f} "
                    f"(universe 100% loose-WR on n=192)"
                )
                c["filter_mtf_dn_carve_calm_seller"] = c.get(
                    "filter_mtf_dn_carve_calm_seller", 0
                ) + 1
            if _mtf_dn_pc_h1_carve and _filter_mtf_dn_block_reasons:
                logger.info(
                    f"[DipScanner] filter_mtf_strong_downtrend RESCUED by pc_h1: "
                    f"{token_symbol} pc_h1={pc_h1:+.1f}%>-20 "
                    f"(realistic sim d=+0.65, winners avg -15.6 vs losers -24.4)"
                )
                c["filter_mtf_dn_carve_pc_h1"] = c.get(
                    "filter_mtf_dn_carve_pc_h1", 0
                ) + 1
            if _mtf_dn_breakthrough_carve:
                logger.info(
                    f"[DipScanner] filter_mtf_strong_downtrend RESCUED by breakthrough_early: "
                    f"{token_symbol} (strong_orderflow or sustained_accum signature)"
                )
                c["filter_mtf_dn_carve_breakthrough"] = c.get(
                    "filter_mtf_dn_carve_breakthrough", 0
                ) + 1

            # ── filter_falling_knife — ENFORCED 2026-05-15 ──────────────────
            # Compound: chart_mtf_score <= -1 AND 1m_last_close_pct < 0.
            # Catches the "stacked-trigger falling knife" pattern from RAGEGUY
            # 2026-05-15 03:05 UTC stop: 4 triggers stacked (alpha_buyperscold,
            # patient_bottom, informed_cluster, whale_conviction) but mtf=-1
            # AND the last 1m bar was still red (-0.83%). Token kept falling
            # post-entry for another -8.5% before the bot stopped out; the
            # actual bottom formed 30+ minutes later.
            #
            # Mechanism: when multi-TF is bearish AND the most recent 1m bar
            # is still red, the dip-buy is catching a falling knife — there
            # is no green confirmation candle yet. Waiting for the next 1m
            # bar to close green (or for mtf to flip) yields a much better
            # entry. Surgically narrower than filter_mtf_strong_downtrend
            # (which blocks mtf<=-2 alone) — this filter targets mtf=-1 (the
            # "mild bearish" zone) but only when the 1m tape confirms the
            # downward continuation.
            #
            # Validation on .audit_trades.json (n=43 paired buys, 2026-05-12
            # to 2026-05-14): BLOCKED n=5, wins=1/5 (20% WR), total $-7.17;
            # PASSED n=38, 37% WR. Net +$7.17/5d. Only 1 small winner blocked
            # (MASCOTS +$1.40); 4 losers caught (HENTAI -$1.69, CHINA -$6.04,
            # ANDV -$0.84, COPIUM $0.00). RAGEGUY (not in audit window) would
            # have been caught by this filter (mtf=-1, 1m_close=-0.83).
            #
            # Fail-open if either feature missing.
            _fk_mtf = None
            try:
                _fk_mtf = _chart_ctx.mtf.get("score") if _chart_ctx else None
            except Exception:
                _fk_mtf = None
            _fk_lcp = m1_features.get("1m_last_close_pct") if isinstance(m1_features, dict) else None
            _filter_falling_knife_block_reasons: list = []
            try:
                if (
                    _fk_mtf is not None and float(_fk_mtf) <= -1.0
                    and _fk_lcp is not None and float(_fk_lcp) < 0.0
                ):
                    _filter_falling_knife_block_reasons.append(
                        f"mtf_score={float(_fk_mtf):.1f}<=-1 AND "
                        f"1m_last_close={float(_fk_lcp):+.2f}%<0 "
                        f"(falling knife — no green confirmation)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] filter_falling_knife err: {_e}")
            _filter_falling_knife_verdict = "BLOCK" if _filter_falling_knife_block_reasons else "PASS"
            c[f"filter_falling_knife_{_filter_falling_knife_verdict.lower()}"] = c.get(
                f"filter_falling_knife_{_filter_falling_knife_verdict.lower()}", 0
            ) + 1
            if _filter_falling_knife_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] SHADOW filter_falling_knife would BLOCK: "
                    f"{token_symbol} reasons={','.join(_filter_falling_knife_block_reasons)}"
                )
                # REVERTED to SHADOW 2026-05-16 PM. Universe audit
                # showed blocked cohort 76.9% WR / +11.45% EV vs
                # passed 73.9% WR / +7.39% EV. Steep dump with seller
                # dominance is a capitulation signal, not a danger flag.

            # ── trigger_mtf_aligned_demand — ENFORCED 2026-05-13 PM ──────────
            # Round-6 mining of entry_meta features (already populated in
            # production — no new fetches). Compound:
            #   chart_mtf_score >= 0.5 (multi-tf alignment confirms direction)
            #   AND 1s_close_pos_60s >= 0.7 (close in top 30% of 60s range)
            # Validation on 29 paired production trades: 4W / 0L = 100%
            # precision. Winners: PAC (+$0.45), TROLLGE (+$0.42), NOGUY
            # (+$0.48), BUFO (+$0.63).
            # Mechanism: when multiple TFs agree direction is up AND the
            # very-recent 60s tape closed in the upper third of its range,
            # both macro (mtf) and micro (1s tape) buyers are aligned.
            _trigger_mtf_aligned_demand_match = False
            _trigger_mtf_aligned_demand_reasons: list = []
            try:
                _mta_mtf = None
                try:
                    _mta_mtf = _chart_ctx.mtf.get("score") if _chart_ctx else None
                except Exception:
                    _mta_mtf = None
                _mta_cp = _1s_features.get("close_pos_60s") if isinstance(_1s_features, dict) else None
                if (_mta_mtf is not None and float(_mta_mtf) >= 0.5
                        and _mta_cp is not None and float(_mta_cp) >= 0.7):
                    _trigger_mtf_aligned_demand_match = True
                    _trigger_mtf_aligned_demand_reasons.append(
                        f"chart_mtf_score={float(_mta_mtf):.1f}>=0.5 "
                        f"AND 1s_close_pos_60s={float(_mta_cp):.2f}>=0.7"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] mtf_aligned_demand err: {_e}")

            # ── trigger_pullback_in_uptrend — ENFORCED 2026-05-13 PM ─────────
            # Round-2 analysis (n=18 paired tokens, 11W vs 6L) plus combined
            # round-1+2 (n=55, 27W vs 27L) found this orthogonal compound:
            #   1h_last3_n_green >= 2  (macro uptrend on hourly TF)
            #   AND 5m_last5_n_green <= 2  (recent 5m pullback within uptrend)
            #   AND last_5m_candle is green  (local turn confirmed)
            # Validation: 5W / 0L = 100% precision across combined 55-token
            # sample. Winners: PAGO (+1.09), RAGEGUY (+0.77), PAC (+0.45),
            # COPPERINU (+0.44), plus 1 from round-1.
            # Mechanism: classic "buy the dip in an established uptrend."
            # Counter-intuitive part: 5m_n_green<=2 means recent 5m is
            # PULLED BACK (not sprinting); sprinting = late entry that fades.
            _trigger_pullback_in_uptrend_match = False
            _trigger_pullback_in_uptrend_reasons: list = []
            try:
                _pu_m5 = (_chart_data.candles_5m
                          if _chart_data and _chart_data.candles_5m else [])
                _pu_h1 = (_chart_data.candles_1h
                          if _chart_data and _chart_data.candles_1h else [])
                # Trigger-scoped volume gate — ENFORCED 2026-05-15 PM.
                # BULLISH 2026-05-15 04:06 UTC: pullback_in_uptrend fired
                # on a token with 1m_volume_spike=0.007 (basically zero
                # vol). Chart was technically a "pullback in uptrend" but
                # the tape was dead — no buyers stepping in. Trade fast-
                # dud'd at -3.9% in 14 min (peak +0.0%). FILTER_1M_SHADOW
                # would have caught this (vol_spike<0.40) but is in
                # shadow; this trigger-scoped gate is the surgical fix.
                _pu_vs = m1_features.get("1m_volume_spike") if isinstance(m1_features, dict) else None
                _pu_vol_ok = _pu_vs is not None and float(_pu_vs) >= 0.30
                if len(_pu_m5) >= 5 and len(_pu_h1) >= 3 and _pu_vol_ok:
                    _pu_1h_g = sum(1 for c in _pu_h1[-3:] if c.close > c.open)
                    _pu_5m_g = sum(1 for c in _pu_m5[-5:] if c.close > c.open)
                    _pu_last_g = _pu_m5[-1].close > _pu_m5[-1].open
                    if _pu_1h_g >= 2 and _pu_5m_g <= 2 and _pu_last_g:
                        _trigger_pullback_in_uptrend_match = True
                        _trigger_pullback_in_uptrend_reasons.append(
                            f"1h_last3_n_green={_pu_1h_g}>=2 "
                            f"AND 5m_last5_n_green={_pu_5m_g}<=2 "
                            f"AND last_5m_green={_pu_last_g} "
                            f"AND 1m_vol_spike={float(_pu_vs):.2f}>=0.30"
                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] pullback_in_uptrend trigger err: {_e}")

            # ── trigger_strong_uptrend_dip — ENFORCED 2026-05-14 PM ─────────
            # 35-angle chart inspection on n=26 post-May-12 paired:
            # Compound D = `1h_6h_chg > 30% AND 1h_4_red <= 1` matched
            # 4 of 9 winners (PAGO+$2.18, BUFO+$1.95, PAC+$0.93, COPPERINU
            # +$0.69) and ZERO of 17 losers = 100% precision (n=4).
            # Mechanism: token up 30%+ over 6h (real bull leg) AND last
            # 4 hourly candles almost-all green (no breakdown). Pullback
            # entries within confirmed bull legs.
            # Will rarely fire in bear macros — that's by design.
            _trigger_strong_uptrend_dip_match = False
            _trigger_strong_uptrend_dip_reasons: list = []
            try:
                _sud_h1 = (_chart_data.candles_1h
                           if _chart_data and _chart_data.candles_1h else [])
                if len(_sud_h1) >= 6:
                    _sud_4_red = sum(1 for c in _sud_h1[-4:] if c.close < c.open)
                    _sud_6h_open = _sud_h1[-6].open
                    _sud_cur_close = _sud_h1[-1].close
                    if _sud_6h_open > 0:
                        _sud_6h_chg = ((_sud_cur_close / _sud_6h_open) - 1) * 100
                        if _sud_6h_chg > 30 and _sud_4_red <= 1:
                            _trigger_strong_uptrend_dip_match = True
                            _trigger_strong_uptrend_dip_reasons.append(
                                f"1h_6h_chg={_sud_6h_chg:+.1f}%>30 "
                                f"AND 1h_4_red={_sud_4_red}<=1 "
                                f"(strong uptrend with minor pullback)"
                            )
            except Exception as _e:
                logger.debug(f"[DipScanner] strong_uptrend_dip trigger err: {_e}")

            # ── trigger_vol_surge_recent — ENFORCED 2026-05-13 PM ───────────
            # Round-2 analysis: vol_recent_vs_long_30d_avg was a Cohen's d
            # +0.83 separator (winners 4.31x, losers 1.46x). Production
            # only has 48h of 1h candles, so we approximate using a
            # recent-8h vs prior-40h ratio (same mechanism: recent surge
            # vs longer baseline, scaled to available history).
            #
            # Validated on round-2 paired set: 7W / 2L hits = 78% precision.
            # 9 fires of 24 tokens with data (38% hit rate among populated).
            # Combined with pullback as either-or: 10W/2L = 83% precision.
            #
            # Threshold 3.0 chosen from round-2 winner median 4.3x; setting
            # 3.0 catches the upper-tail-surge signal without overfitting.
            # Skip when prior_40h_avg is zero (token too new for baseline).
            _trigger_vol_surge_recent_match = False
            _trigger_vol_surge_recent_reasons: list = []
            try:
                _vsr_h1 = (_chart_data.candles_1h
                           if _chart_data and _chart_data.candles_1h else [])
                if len(_vsr_h1) >= 12:  # need at least 12 1h candles
                    _vsr_recent_n = min(8, max(4, len(_vsr_h1) // 6))
                    _vsr_recent = _vsr_h1[-_vsr_recent_n:]
                    _vsr_prior = _vsr_h1[:-_vsr_recent_n]
                    if _vsr_prior:
                        _vsr_recent_avg = (
                            sum(c.volume for c in _vsr_recent) / len(_vsr_recent)
                        )
                        _vsr_prior_avg = (
                            sum(c.volume for c in _vsr_prior) / len(_vsr_prior)
                        )
                        if _vsr_prior_avg > 0:
                            _vsr_ratio = _vsr_recent_avg / _vsr_prior_avg
                            if _vsr_ratio >= 3.0:
                                _trigger_vol_surge_recent_match = True
                                _trigger_vol_surge_recent_reasons.append(
                                    f"vol_recent_{_vsr_recent_n}h_avg/"
                                    f"vol_prior_{len(_vsr_prior)}h_avg="
                                    f"{_vsr_ratio:.2f}>=3.0 "
                                    f"(recent {_vsr_recent_n}h volume "
                                    f"{_vsr_ratio:.1f}x longer baseline)"
                                )
            except Exception as _e:
                logger.debug(f"[DipScanner] vol_surge_recent trigger err: {_e}")

            # ── trigger_bullish_engulfing_5m — ENFORCED 2026-05-13 PM ────────
            # Round-3 pattern mining (n=55 combined paired tokens). Last 2
            # 5m bars form a textbook bullish engulfing:
            #   prior bar is red AND current is green
            #   current open <= prior close (gap below or equal)
            #   current close >= prior open (engulfs the body)
            #   current body > prior body (decisive reversal)
            # Validation: 6W / 0L = 100% precision on 55 paired sample.
            # Winners: PAGO (+1.09), RAGEGUY (+0.77), NOGUY (+0.48),
            # COPPERINU (+0.44), plus 2 others.
            # Mechanism: classic candlestick reversal — buyers absorbed
            # the prior down bar and pushed past its high.
            # No peak_h24 gate (signal is strong standalone; gating would
            # block RAGEGUY/COPPERINU winners).
            _trigger_bullish_engulfing_5m_match = False
            _trigger_bullish_engulfing_5m_reasons: list = []
            try:
                _be_m5 = (_chart_data.candles_5m
                          if _chart_data and _chart_data.candles_5m else [])
                if len(_be_m5) >= 2:
                    _be_c1 = _be_m5[-2]; _be_c2 = _be_m5[-1]
                    _be_c1_body = abs(_be_c1.close - _be_c1.open)
                    _be_c2_body = abs(_be_c2.close - _be_c2.open)
                    if (_be_c1.close < _be_c1.open  # prior red
                            and _be_c2.close > _be_c2.open  # current green
                            and _be_c2.open <= _be_c1.close
                            and _be_c2.close >= _be_c1.open
                            and _be_c2_body > _be_c1_body):
                        _trigger_bullish_engulfing_5m_match = True
                        _trigger_bullish_engulfing_5m_reasons.append(
                            f"5m_bullish_engulfing: "
                            f"prior_red_body={_be_c1_body:.6f} -> "
                            f"current_green_body={_be_c2_body:.6f} "
                            f"(ratio={_be_c2_body/max(_be_c1_body,1e-12):.2f}x)"
                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] bullish_engulfing_5m err: {_e}")

            # ── trigger_controlled_greens_5m — ENFORCED 2026-05-13 PM ─────────
            # Catches "measured uptrend forming" pattern: ≥4 of last 8 5m
            # candles are green AND not marubozu (body/range < 0.80). This
            # is the largest single-pattern WIN/LOSS separator found in the
            # deep candle analysis (5m_dex green_normal: 42.8% W vs 26.0% L,
            # diff +16.8pp on n=635 candles).
            # Sample evidence: 14% W hit + 38% SW hit + 7% L hit → 83% precision.
            # Winners: 2ryWMuYm5g3o (MID_WIN +1.07), Hf8RNuWd4DLv (MID_WIN
            # +1.13). Small wins: 33eum82LaAhtv5 (+0.63), 8J69rbLTzWWgUJ (+0.59).
            #
            # GATED by peak_h24_6h >= 200%. Same scoping as extreme_sweep_1m.
            _trigger_controlled_greens_5m_match = False
            _trigger_controlled_greens_5m_reasons: list = []
            try:
                _cg_cs = (_chart_data.candles_5m
                          if _chart_data and _chart_data.candles_5m else [])
                if len(_cg_cs) >= 8:
                    _cg_last8 = _cg_cs[-8:]
                    _cg_n_norm_green = 0
                    for _c in _cg_last8:
                        if _c.close <= _c.open:  # not green
                            continue
                        _b = abs(_c.close - _c.open)
                        _rng = _c.high - _c.low
                        if _rng <= 0:
                            continue
                        if (_b / _rng) < 0.80:  # not marubozu
                            _cg_n_norm_green += 1
                    _cg_peak = float(peak_h24_6h) if peak_h24_6h is not None else 0.0
                    # TIGHTENED 2026-05-13 PM (round-5): require last 5m green.
                    # Original (n_norm_green>=4 + peak>=200) had 73% precision
                    # on n=55. Adding last_5m_green requirement: 6W/0L = 100%
                    # precision (blocks CHINA -$6.04 + 2 others, costs RKC +$1.16,
                    # HANTA +$1.09. Net save: +$5.89).
                    _cg_last_green = (
                        _cg_last8[-1].close > _cg_last8[-1].open
                    )
                    if (_cg_n_norm_green >= 4
                            and _cg_peak >= 200.0
                            and _cg_last_green):
                        _trigger_controlled_greens_5m_match = True
                        _trigger_controlled_greens_5m_reasons.append(
                            f"5m_normal_greens_in_last_8={_cg_n_norm_green}>=4 "
                            f"(body/range<0.80) AND "
                            f"peak_h24_6h={_cg_peak:.0f}%>=200 AND "
                            f"last_5m_green=True"
                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] controlled_greens_5m trigger err: {_e}")

            _trigger_demand_bottom_match = False
            _trigger_demand_bottom_reasons: list = []
            try:
                _bst_raw = None
                try:
                    _bst_raw = (_trade_log_dict or {}).get("buy_size_max_trend")
                except Exception:
                    _bst_raw = None
                _bst = float(_bst_raw) if _bst_raw is not None else None
                _cs_raw = (_chart_ctx_dict or {}).get("chart_score") if isinstance(_chart_ctx_dict, dict) else None
                _cs = float(_cs_raw) if _cs_raw is not None else None
                _cswp = (_chart_ctx_dict or {}).get("chart_sweep_5m_verdict") if isinstance(_chart_ctx_dict, dict) else None
                _gs = None
                try:
                    _gs = (_graduation_dict or {}).get("graduation_status")
                except Exception:
                    _gs = None
                _peak24_dbc = float(peak_h24_6h) if peak_h24_6h is not None else 0.0

                # B1: post-pump + escalating demand
                if (_bst is not None and _bst >= 2.0
                        and _peak24_dbc >= 500):
                    _trigger_demand_bottom_match = True
                    _trigger_demand_bottom_reasons.append(
                        f"B1 buy_size_max_trend={_bst:.2f}>=2 AND peak24={_peak24_dbc:.0f}%>=500"
                    )
                # B2: fresh graduate + escalating demand
                elif (_gs == 'just_graduated'
                      and _bst is not None and _bst >= 2.0):
                    _trigger_demand_bottom_match = True
                    _trigger_demand_bottom_reasons.append(
                        f"B2 just_graduated AND buy_size_max_trend={_bst:.2f}>=2"
                    )
                # B3: confirmed bullish sweep + post-pump + decent chart
                elif (_cswp == 'BULLISH_SWEEP'
                      and _peak24_dbc >= 500
                      and _cs is not None and _cs >= 50):
                    _trigger_demand_bottom_match = True
                    _trigger_demand_bottom_reasons.append(
                        f"B3 BULLISH_SWEEP AND peak24={_peak24_dbc:.0f}%>=500 AND chart_score={_cs:.0f}>=50"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] demand_bottom trigger err: {_e}")

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
                    elif (
                        _lp_flow_dict is not None
                        and isinstance(_lp_flow_dict.get("lp_delta_15m_pct"), (int, float))
                        and float(_lp_flow_dict["lp_delta_15m_pct"]) < -1.0
                    ):
                        # Gate E: lp_delta_15m_pct < -1.0 — ENFORCED 2026-05-10.
                        # Smart-LP-exit gate. When liquidity is draining over the
                        # last 15 min, the buyers behind the 1m green candle aren't
                        # there to chase — entry round-trips. Validated on lifetime
                        # clean_break cohort (n=83): blocks 28/83 (33.7%), 25.0% WR
                        # blocked, save:cut 4.66×, net +$46.81. Held-out (n=17):
                        # blocks 6/17, 16.7% WR, save:cut 10.12×, net +$12.31 — the
                        # gate is anti-overfit (held-out > lifetime sc). Trigger
                        # case: 2026-05-09 evening 6-loss streak, 4 of 6 had
                        # lp_delta < -1 (DATA -3.2, POGE -7.4, CONSENSUS-1 -4.2,
                        # Goblin -2.6); the other 2 (aura, Hoppy) had no LP data
                        # → fail-open preserves throughput on young pools.
                        # Hard gate, runs before compound override. Mechanism:
                        # LP providers exiting before the candle is the strongest
                        # "no follow-through" signal we have for clean_break.
                        _cb_lp_d = float(_lp_flow_dict["lp_delta_15m_pct"])
                        _cb_gated = True
                        _cb_gate_reason = (
                            f"lp_delta_15m={_cb_lp_d:+.1f}%<-1.0 "
                            f"(LP draining — smart-money exit, no follow-through)"
                        )
                    elif (
                        # Gate F: h24_ratio_to_peak [0.80, 0.95) dead zone.
                        # ENFORCED 2026-05-14 PM. Mining audit (n=246 clean_break
                        # solo lifetime): ratio 0.80-0.95 = 29.3% WR, -$30.50
                        # across 41 fires — single largest losing sub-cohort.
                        # Mid-retracement-recovery zone where bounces stall
                        # and re-dump before TP. Same pattern caught the
                        # whale_conviction RAGEGUY 17:14 -4.86% loser.
                        # Hard gate (runs before compound override): the
                        # dead zone is a structural entry-quality problem
                        # that compound triggers can't fix.
                        (_lifecycle_dict or {}).get("lifecycle_h24_ratio") is not None
                        and 0.80 <= float((_lifecycle_dict or {}).get("lifecycle_h24_ratio")) < 0.95
                    ):
                        _cb_ratio_dz = float((_lifecycle_dict or {}).get("lifecycle_h24_ratio"))
                        _cb_gated = True
                        _cb_gate_reason = (
                            f"h24_ratio_to_peak={_cb_ratio_dz:.2f} in dead zone "
                            f"[0.80, 0.95) — mid-retracement-recovery, see audit"
                        )
                    elif (
                        # Gate G: bearish-mtf OR weak-chart context.
                        # ENFORCED 2026-05-15 PM. Recent 3d audit (n=11 clean_break
                        # fires) showed 27% WR / -$17.15 net. Cohen-d separation
                        # of winners vs losers: chart_mtf_score (W +0 / L -1,
                        # sep +0.96), chart_score (W 50.6 / L 45.1, sep +1.17).
                        # Compound gate `mtf >= 0 AND chart_score >= 48` blocks
                        # 7 of 8 losers (-$15.58 saved), 1 winner blocked (RKC
                        # +$0.84). Backtest: 4 kept / 50% WR / -$1.57 net (vs
                        # baseline -$17.15). Hard gate matches Gate F precedent
                        # — bearish-mtf + low-chart-score is a structural entry-
                        # quality problem that compound triggers can't fix.
                        (
                            (_chart_ctx_dict or {}).get("chart_mtf_score") is None
                            or float((_chart_ctx_dict or {}).get("chart_mtf_score")) < 0
                        )
                        or (
                            (_chart_ctx_dict or {}).get("chart_score") is None
                            or float((_chart_ctx_dict or {}).get("chart_score")) < 48
                        )
                    ):
                        _cb_mtf_g = (_chart_ctx_dict or {}).get("chart_mtf_score")
                        _cb_cs_g = (_chart_ctx_dict or {}).get("chart_score")
                        _cb_gated = True
                        _cb_gate_reason = (
                            f"mtf={_cb_mtf_g} or chart_score={_cb_cs_g} fails "
                            f"compound gate (need mtf>=0 AND chart_score>=48)"
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

            # ── trigger_strong_orderflow — ENFORCED 2026-05-15 ───────────
            # Three independent on-chain dimensions all aligned positive:
            #   net_flow_60s_usd > 0   (real $ buyer flow in last 60s)
            #   chart_mtf_score >= 1   (multi-tf bullish alignment)
            #   bs_m5 >= 1.5           (5m txn-count ratio buyer-side)
            #
            # Mined from lifetime closed paired trades (n=58, 34.5% baseline
            # WR). The compound predicate had 8/8 wins, +$6.08 net. Three
            # axes are structurally independent (orderbook reality, chart
            # structure, txn-count flow) — so the joint signal isn't
            # collinear with any single dimension.
            #
            # Binomial significance: probability of 8/8 wins under H0 of
            # WR=0.345 ≈ 0.018%. Real signal, not multiple-testing artifact
            # (this was the first compound predicate tested with mtf+flow+bs).
            #
            # Monitor: revert if forward WR drops below 60% on next 10 fires.
            _trigger_strong_orderflow_match = False
            _trigger_strong_orderflow_reasons: list = []
            try:
                _nf60s_so = _tier3_features.get("net_flow_60s_usd") if isinstance(_tier3_features, dict) else None
                _mtf_so = (_chart_ctx_dict or {}).get("chart_mtf_score") if isinstance(_chart_ctx_dict, dict) else None
                if (_nf60s_so is not None and float(_nf60s_so) > 0
                        and _mtf_so is not None and float(_mtf_so) >= 1.0
                        and ratio_m5 != float("inf") and ratio_m5 >= 1.5):
                    _trigger_strong_orderflow_match = True
                    _trigger_strong_orderflow_reasons.append(
                        f"net_flow_60s_usd=${float(_nf60s_so):+.0f}>0 AND "
                        f"chart_mtf_score={float(_mtf_so):.1f}>=1.0 AND "
                        f"bs_m5={ratio_m5:.2f}>=1.5"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_strong_orderflow err: {_e}")
            if _trigger_strong_orderflow_match:
                logger.info(
                    f"[DipScanner] trigger_strong_orderflow FIRED: {token_symbol} "
                    f"{';'.join(_trigger_strong_orderflow_reasons)}"
                )

            # ── trigger_sustained_accumulation — ENFORCED 2026-05-15 ────
            # Multi-window buyer dominance + positive 60s flow + mtf neutral+.
            # Lifetime: 7/7 wins, +$5.47 net (flow60>0 & bs_h1>=1.5 & bs_h6>=1.2 & mtf>=0).
            # Different signal than strong_orderflow — captures SUSTAINED
            # accumulation (m5, h1, h6 all buyer-leaning) vs strong_orderflow
            # which is m5-only.
            _trigger_sustained_accum_match = False
            _trigger_sustained_accum_reasons: list = []
            try:
                _nf60_sa = _tier3_features.get("net_flow_60s_usd") if isinstance(_tier3_features, dict) else None
                _mtf_sa = (_chart_ctx_dict or {}).get("chart_mtf_score") if isinstance(_chart_ctx_dict, dict) else None
                _bs_h1_sa = float(ratio_h1) if ratio_h1 != float("inf") else None
                _bs_h6_sa = float(ratio_h6) if ratio_h6 != float("inf") else None
                if (_nf60_sa is not None and float(_nf60_sa) > 0
                        and _mtf_sa is not None and float(_mtf_sa) >= 0
                        and _bs_h1_sa is not None and _bs_h1_sa >= 1.5
                        and _bs_h6_sa is not None and _bs_h6_sa >= 1.2):
                    _trigger_sustained_accum_match = True
                    _trigger_sustained_accum_reasons.append(
                        f"net_flow_60s=${float(_nf60_sa):+.0f}>0 AND "
                        f"bs_h1={_bs_h1_sa:.2f}>=1.5 AND bs_h6={_bs_h6_sa:.2f}>=1.2 AND "
                        f"mtf={float(_mtf_sa):.1f}>=0"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_sustained_accum err: {_e}")
            if _trigger_sustained_accum_match:
                logger.info(
                    f"[DipScanner] trigger_sustained_accumulation FIRED: {token_symbol} "
                    f"{';'.join(_trigger_sustained_accum_reasons)}"
                )

            # ── trigger_chart_quality_bottom — ENFORCED 2026-05-15 ──────
            # Chart-anchored bottom: mtf neutral+, chart_score>=50 (decent
            # structure), 1s_bottom_score>=20 (bot's own composite signal).
            # Lifetime: 6/7 wins, +$3.60 net. Captures high-quality
            # chart bottoms where on-chain flow is silent. Complement to
            # strong_orderflow which is flow-driven.
            _trigger_chart_qual_bottom_match = False
            _trigger_chart_qual_bottom_reasons: list = []
            try:
                _mtf_cq = (_chart_ctx_dict or {}).get("chart_mtf_score") if isinstance(_chart_ctx_dict, dict) else None
                _csc_cq = (_chart_ctx_dict or {}).get("chart_score") if isinstance(_chart_ctx_dict, dict) else None
                _1sb_cq = _1s_features.get("bottom_score") if isinstance(_1s_features, dict) else None
                if (_mtf_cq is not None and float(_mtf_cq) >= 0
                        and _csc_cq is not None and float(_csc_cq) >= 50
                        and _1sb_cq is not None and float(_1sb_cq) >= 20):
                    _trigger_chart_qual_bottom_match = True
                    _trigger_chart_qual_bottom_reasons.append(
                        f"mtf={float(_mtf_cq):.1f}>=0 AND chart_score={float(_csc_cq):.1f}>=50 AND "
                        f"1s_bottom_score={float(_1sb_cq):.1f}>=20"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_chart_qual_bottom err: {_e}")
            if _trigger_chart_qual_bottom_match:
                logger.info(
                    f"[DipScanner] trigger_chart_quality_bottom FIRED: {token_symbol} "
                    f"{';'.join(_trigger_chart_qual_bottom_reasons)}"
                )

            # ── trigger_buyer_momentum_burst — ENFORCED 2026-05-15 ──────
            # Active buyer momentum: 3+ buy bursts in 30s + real $ flowing
            # + price in upper half of 5m range (not catching knife).
            # Lifetime: 8/11 wins (72.7% WR), +$3.00 net.
            # Largest-n high-WR compound found. Distinct from flow triggers:
            # this is BURSTY activity (FOMO entry) vs steady flow.
            _trigger_buyer_momentum_burst_match = False
            _trigger_buyer_momentum_burst_reasons: list = []
            try:
                # buy_burst_30s_count is from smart_money / order flow features
                _bb_30s = _tier3_features.get("buy_burst_30s_count") if isinstance(_tier3_features, dict) else None
                if _bb_30s is None:
                    _bb_30s = _tier1_features.get("buy_burst_30s_count") if isinstance(_tier1_features, dict) else None
                _rt_buys_usd_b = _tier3_features.get("rt_buys_usd") if isinstance(_tier3_features, dict) else None
                if _rt_buys_usd_b is None:
                    _rt_buys_usd_b = _tier1_features.get("rt_buys_usd") if isinstance(_tier1_features, dict) else None
                _pct_5m_rng = (_chart_ctx_dict or {}).get("pct_in_5m_range") if isinstance(_chart_ctx_dict, dict) else None
                if (_bb_30s is not None and int(_bb_30s) >= 3
                        and _rt_buys_usd_b is not None and float(_rt_buys_usd_b) > 500
                        and _pct_5m_rng is not None and float(_pct_5m_rng) > 0.5):
                    _trigger_buyer_momentum_burst_match = True
                    _trigger_buyer_momentum_burst_reasons.append(
                        f"buy_bursts_30s={int(_bb_30s)}>=3 AND "
                        f"rt_buys_usd=${float(_rt_buys_usd_b):.0f}>500 AND "
                        f"pct_in_5m_range={float(_pct_5m_rng):.2f}>0.5"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_buyer_momentum_burst err: {_e}")
            if _trigger_buyer_momentum_burst_match:
                logger.info(
                    f"[DipScanner] trigger_buyer_momentum_burst FIRED: {token_symbol} "
                    f"{';'.join(_trigger_buyer_momentum_burst_reasons)}"
                )

            # ── trigger_flow_reversal — ENFORCED 2026-05-15 ──────────────
            # Pure reversal-from-decline signal: token has been declining
            # on 6h AND real-time flow is positive AND the most recent 60s
            # are closing in the upper half of range = reversal in progress.
            # Lifetime: 9/11 wins (81.8% WR), +$4.87 net.
            # LARGEST-n high-WR compound. Captures the DIAMOND/VILLAGEBOY
            # signature: dip-and-recover within a longer trend.
            _trigger_flow_reversal_match = False
            _trigger_flow_reversal_reasons: list = []
            try:
                _nf60_fr = _tier3_features.get("net_flow_60s_usd") if isinstance(_tier3_features, dict) else None
                _pc_h6_fr = pc_h6  # already in scope from pair priceChange
                _1s_close_pos = _1s_features.get("close_pos_60s") if isinstance(_1s_features, dict) else None
                if (_nf60_fr is not None and float(_nf60_fr) > 0
                        and isinstance(_pc_h6_fr, (int, float)) and float(_pc_h6_fr) < 0
                        and _1s_close_pos is not None and float(_1s_close_pos) > 0.5):
                    _trigger_flow_reversal_match = True
                    _trigger_flow_reversal_reasons.append(
                        f"net_flow_60s=${float(_nf60_fr):+.0f}>0 AND "
                        f"pc_h6={float(_pc_h6_fr):+.1f}%<0 AND "
                        f"1s_close_pos={float(_1s_close_pos):.2f}>0.5"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_flow_reversal err: {_e}")
            if _trigger_flow_reversal_match:
                logger.info(
                    f"[DipScanner] trigger_flow_reversal FIRED: {token_symbol} "
                    f"{';'.join(_trigger_flow_reversal_reasons)}"
                )

            # ── trigger_chart_score_reversal — ENFORCED 2026-05-15 ─────
            # Reversal from decline confirmed by chart structure quality:
            # token has pc_h6 negative AND chart_score>=50 (decent shape)
            # AND 1s_bottom_score>=20 (bottom signature). Lifetime: 8/9
            # wins (88.9% WR), +$4.29 net. Differentiated from chart_qual_
            # bottom (which doesn't constrain pc_h6) — this fires only on
            # reversal-of-decline shapes.
            _trigger_chart_reversal_match = False
            _trigger_chart_reversal_reasons: list = []
            try:
                _csc_cr = (_chart_ctx_dict or {}).get("chart_score") if isinstance(_chart_ctx_dict, dict) else None
                _bsc_cr = _1s_features.get("bottom_score") if isinstance(_1s_features, dict) else None
                _pc_h6_cr = pc_h6
                if (_csc_cr is not None and float(_csc_cr) >= 50
                        and _bsc_cr is not None and float(_bsc_cr) >= 20
                        and isinstance(_pc_h6_cr, (int, float)) and float(_pc_h6_cr) < 0):
                    _trigger_chart_reversal_match = True
                    _trigger_chart_reversal_reasons.append(
                        f"chart_score={float(_csc_cr):.1f}>=50 AND "
                        f"1s_bottom_score={float(_bsc_cr):.1f}>=20 AND "
                        f"pc_h6={float(_pc_h6_cr):+.1f}%<0"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_chart_reversal err: {_e}")
            if _trigger_chart_reversal_match:
                logger.info(
                    f"[DipScanner] trigger_chart_score_reversal FIRED: {token_symbol} "
                    f"{';'.join(_trigger_chart_reversal_reasons)}"
                )

            # ── trigger_swing_structure_rsi (R2-T1) — ENFORCED 2026-05-17 ──
            # Chart-pattern compound mined from 90-trade dataset.
            # chart_structure_5m_swing_count >= 28 AND rsi_15m >= 51.61 AND
            # chart_trendline_5m_pct_to_support >= 2.15.
            # Lifetime: 8/9 wins (88.9% WR), binomial p=0.0031 vs 38.9% baseline,
            # avg +1.30%, mean_buy_size_usd $108 (wash-guarded).
            # Captures: well-structured 5m chart (high swing count = many
            # pivots), 15m bullish momentum (rsi extended above neutral),
            # entry well above support (>2.15% buffer for downside).
            # Spans 5 days, 7 unique tokens (CLUDE/PAC/COPPERINU/RAGEGUY/
            # FAHHHH/AINL/VIRL). Zero feature overlap with original 6.
            _trigger_swing_structure_rsi_match = False
            _trigger_swing_structure_rsi_reasons: list = []
            try:
                _ssr_swing = (_chart_ctx_dict or {}).get("chart_structure_5m_swing_count") if isinstance(_chart_ctx_dict, dict) else None
                _ssr_rsi = _tier2_features.get("rsi_15m") if isinstance(_tier2_features, dict) else None
                _ssr_pcts = (_chart_ctx_dict or {}).get("chart_trendline_5m_pct_to_support") if isinstance(_chart_ctx_dict, dict) else None
                if (_ssr_swing is not None and float(_ssr_swing) >= 28
                        and _ssr_rsi is not None and float(_ssr_rsi) >= 51.61
                        and _ssr_pcts is not None and float(_ssr_pcts) >= 2.15):
                    _trigger_swing_structure_rsi_match = True
                    _trigger_swing_structure_rsi_reasons.append(
                        f"swing_count={float(_ssr_swing):.0f}>=28 AND "
                        f"rsi_15m={float(_ssr_rsi):.1f}>=51.61 AND "
                        f"pct_to_support={float(_ssr_pcts):.2f}%>=2.15"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_swing_structure_rsi err: {_e}")
            if _trigger_swing_structure_rsi_match:
                logger.info(
                    f"[DipScanner] trigger_swing_structure_rsi FIRED: {token_symbol} "
                    f"{';'.join(_trigger_swing_structure_rsi_reasons)}"
                )

            # ── trigger_channel_pos_swing (R2-T2) — ENFORCED 2026-05-17 ──
            # Chart-pattern compound: 5m trendline channel position high AND
            # many swing lows found. chart_trendline_5m_channel_pos >= 26.4
            # AND n_swing_lows_found >= 28.
            # Lifetime: 6/6 wins (100% WR), binomial p=0.0035, avg +3.75%,
            # mean_buy_size_usd $97 (wash-guarded).
            # Captures: token sitting in upper portion of 5m trendline channel
            # (bullish positioning) AND mature chart with many established
            # support pivots (n_swing_lows >= 28 = well-tested support).
            # Spans 4 days, 5 unique tokens.
            _trigger_channel_pos_swing_match = False
            _trigger_channel_pos_swing_reasons: list = []
            try:
                _cps_pos = (_chart_ctx_dict or {}).get("chart_trendline_5m_channel_pos") if isinstance(_chart_ctx_dict, dict) else None
                _cps_swing = _tier2_features.get("n_swing_lows_found") if isinstance(_tier2_features, dict) else None
                if (_cps_pos is not None and float(_cps_pos) >= 26.40
                        and _cps_swing is not None and float(_cps_swing) >= 28):
                    _trigger_channel_pos_swing_match = True
                    _trigger_channel_pos_swing_reasons.append(
                        f"channel_pos={float(_cps_pos):.1f}%>=26.4 AND "
                        f"n_swing_lows={float(_cps_swing):.0f}>=28"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_channel_pos_swing err: {_e}")
            if _trigger_channel_pos_swing_match:
                logger.info(
                    f"[DipScanner] trigger_channel_pos_swing FIRED: {token_symbol} "
                    f"{';'.join(_trigger_channel_pos_swing_reasons)}"
                )

            # ── trigger_channel_hvn (R3-T3) — ENFORCED 2026-05-17 ──
            # chart_trendline_1h_in_channel>=1 AND chart_vp_at_hvn>=1.
            # Lifetime: 9/12 (75% WR), p=0.0124, avg +1.48%, mean_buy $97.
            # Broadest R3 catcher — 10 of its 12 matched trades are NOT
            # covered by T1/T2 (different signal cohort).
            # Captures: 1h trendline in_channel (range-bound, not breaking
            # down) + price at HVN (high-volume node = strong support).
            _trigger_channel_hvn_match = False
            _trigger_channel_hvn_reasons: list = []
            try:
                _chv_inch = (_chart_ctx_dict or {}).get("chart_trendline_1h_in_channel") if isinstance(_chart_ctx_dict, dict) else None
                _chv_hvn = (_chart_ctx_dict or {}).get("chart_vp_at_hvn") if isinstance(_chart_ctx_dict, dict) else None
                _chv_inch_v = (1 if _chv_inch is True else (0 if _chv_inch is False else (float(_chv_inch) if _chv_inch is not None else None)))
                _chv_hvn_v = (1 if _chv_hvn is True else (0 if _chv_hvn is False else (float(_chv_hvn) if _chv_hvn is not None else None)))
                if (_chv_inch_v is not None and _chv_inch_v >= 1
                        and _chv_hvn_v is not None and _chv_hvn_v >= 1):
                    _trigger_channel_hvn_match = True
                    _trigger_channel_hvn_reasons.append(
                        f"1h_in_channel={_chv_inch_v} AND vp_at_hvn={_chv_hvn_v}"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_channel_hvn err: {_e}")
            if _trigger_channel_hvn_match:
                logger.info(
                    f"[DipScanner] trigger_channel_hvn FIRED: {token_symbol} "
                    f"{';'.join(_trigger_channel_hvn_reasons)}"
                )

            # ── trigger_shape_wick (R3-T4) — ENFORCED 2026-05-17 ──
            # shape_60m_mins_since_max<=18 AND wick_body_5m_avg<=0.40.
            # Lifetime: 8/10 (80% WR), p=0.0101, avg +1.58%, mean_buy ~$90.
            # 8 of 10 matched trades are NOT covered by T1/T2/R3-T3.
            # Captures: recent peak within last 18 minutes (fresh leg) AND
            # tight 5m candles (wick:body <= 0.40 = clean directional bars,
            # not noisy whippy candles).
            _trigger_shape_wick_match = False
            _trigger_shape_wick_reasons: list = []
            try:
                _sw_mins = (_chart_ctx_dict or {}).get("shape_60m_mins_since_max") if isinstance(_chart_ctx_dict, dict) else None
                _sw_wb = _tier3_features.get("wick_body_5m_avg") if isinstance(_tier3_features, dict) else None
                if (_sw_mins is not None and float(_sw_mins) <= 18
                        and _sw_wb is not None and float(_sw_wb) <= 0.40):
                    _trigger_shape_wick_match = True
                    _trigger_shape_wick_reasons.append(
                        f"mins_since_max={float(_sw_mins):.0f}<=18 AND "
                        f"wick_body_5m={float(_sw_wb):.2f}<=0.40"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_shape_wick err: {_e}")
            if _trigger_shape_wick_match:
                logger.info(
                    f"[DipScanner] trigger_shape_wick FIRED: {token_symbol} "
                    f"{';'.join(_trigger_shape_wick_reasons)}"
                )

            # ── trigger_cnn_lp (R3-T6) — ENFORCED 2026-05-17 ──
            # cnn_cluster_id>=9 AND lp_delta_15m_pct>=1.98.
            # Lifetime: 6/6 (100% WR), p=0.0035, avg +3.24%, mean_buy $97.
            # 5 of 6 NEW. Captures: CNN-clustered as a non-rug pattern
            # (cluster_id>=9 excludes the rug-cluster 0-8 region) AND
            # LP growing (15m LP delta >= +1.98% = active liquidity adds,
            # not drains).
            _trigger_cnn_lp_match = False
            _trigger_cnn_lp_reasons: list = []
            try:
                # BUG FIX 2026-05-17: cnn_cluster_id lives in `_cnn_cluster_id`
                # bare local (assigned at line ~2490 from CNN classify call).
                # Previous code looked in undefined `entry_meta_dict_partial`
                # and an empty `_chart_ctx_dict` key, so trigger never fired.
                _cl_cnn = _cnn_cluster_id
                _cl_lp = _tier3_features.get("lp_delta_15m_pct") if isinstance(_tier3_features, dict) else None
                if (_cl_cnn is not None and float(_cl_cnn) >= 9
                        and _cl_lp is not None and float(_cl_lp) >= 1.98):
                    _trigger_cnn_lp_match = True
                    _trigger_cnn_lp_reasons.append(
                        f"cnn_cluster={float(_cl_cnn):.0f}>=9 AND "
                        f"lp_delta_15m={float(_cl_lp):.2f}%>=1.98"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_cnn_lp err: {_e}")
            if _trigger_cnn_lp_match:
                logger.info(
                    f"[DipScanner] trigger_cnn_lp FIRED: {token_symbol} "
                    f"{';'.join(_trigger_cnn_lp_reasons)}"
                )

            # ── trigger_clean_consec_ll (R4-T7) — ENFORCED 2026-05-17 ──
            # bundle_v2_suspected<=0 AND trend_60m_consec_ll>=2.
            # Lifetime: 8/11 (72.7% WR), p=0.018, avg +1.74%, mean_buy ~$90.
            # 4 of 11 matches are NEW (not covered by T1/T2/R3-T3/T4/T6).
            # Captures: clean (non-bundle-suspected) token AND established
            # downtrend now potentially exhausted (2+ consecutive lower
            # lows on 60m = the dip has been forming).
            _trigger_clean_consec_ll_match = False
            _trigger_clean_consec_ll_reasons: list = []
            try:
                _ccl_bund = (_tier1_features or {}).get("bundle_v2_suspected") if isinstance(_tier1_features, dict) else None
                _ccl_ll = (_chart_ctx_dict or {}).get("trend_60m_consec_ll") if isinstance(_chart_ctx_dict, dict) else None
                _ccl_bund_v = (1 if _ccl_bund is True else (0 if _ccl_bund is False else (float(_ccl_bund) if _ccl_bund is not None else None)))
                if (_ccl_bund_v is not None and _ccl_bund_v <= 0
                        and _ccl_ll is not None and float(_ccl_ll) >= 2):
                    _trigger_clean_consec_ll_match = True
                    _trigger_clean_consec_ll_reasons.append(
                        f"bundle_v2_suspected={_ccl_bund_v}<=0 AND "
                        f"trend_60m_consec_ll={float(_ccl_ll):.0f}>=2"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_clean_consec_ll err: {_e}")
            if _trigger_clean_consec_ll_match:
                logger.info(
                    f"[DipScanner] trigger_clean_consec_ll FIRED: {token_symbol} "
                    f"{';'.join(_trigger_clean_consec_ll_reasons)}"
                )

            # ── trigger_sweep_holder_liq (R5-T8) — ENFORCED 2026-05-17 ──
            # chart_sweep_15m_high_recent>=1 AND top10_holder_pct<=41.30
            # AND liq_velocity_h1_usd_per_txn>=124.12.
            # Lifetime: 7/7 wins (100% WR), p=0.0013, avg +4.08%, mean_buy ~$100.
            # 3 of 7 matches are NEW (not covered by T1/T2/R3-T3/T4/T6/R4-T7).
            # Captures: recent 15m high sweep (bearish liquidity grab + reversal)
            # AND distributed top10 holders (<=41% = no whale concentration)
            # AND active large-trade flow ($124/txn = institutional/whale buys).
            _trigger_sweep_holder_liq_match = False
            _trigger_sweep_holder_liq_reasons: list = []
            try:
                _shl_sweep = (_chart_ctx_dict or {}).get("chart_sweep_15m_high_recent") if isinstance(_chart_ctx_dict, dict) else None
                _shl_sweep_v = (1 if _shl_sweep is True else (0 if _shl_sweep is False else (float(_shl_sweep) if _shl_sweep is not None else None)))
                _shl_t10 = top10_holder_pct if 'top10_holder_pct' in dir() else None
                _shl_lv = (volume_velocity_features or {}).get("liq_velocity_h1_usd_per_txn") if isinstance(volume_velocity_features, dict) else None
                if (_shl_sweep_v is not None and _shl_sweep_v >= 1
                        and _shl_t10 is not None and float(_shl_t10) <= 41.30
                        and _shl_lv is not None and float(_shl_lv) >= 124.12):
                    _trigger_sweep_holder_liq_match = True
                    _trigger_sweep_holder_liq_reasons.append(
                        f"sweep_15m_high=1 AND top10={float(_shl_t10):.1f}%<=41.30 AND "
                        f"liq_velocity_h1=${float(_shl_lv):.0f}/txn>=124.12"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_sweep_holder_liq err: {_e}")
            if _trigger_sweep_holder_liq_match:
                logger.info(
                    f"[DipScanner] trigger_sweep_holder_liq FIRED: {token_symbol} "
                    f"{';'.join(_trigger_sweep_holder_liq_reasons)}"
                )

            # ── trigger_clean_dip_trend (R6-T9) — ENFORCED 2026-05-17 ──
            # dip_volume_ratio<=0.88 AND wick_body_5m_max<=4.25
            # AND trend_15m_r_squared>=0.49.
            # Lifetime: 8/10 (80% WR), p=0.0101, avg +3.16%.
            # 2 of 10 matches NEW (after 7 prior triggers).
            # Captures: dip volume falling off (<88% of average = dip
            # exhausting) AND clean 5m bars (max wick:body <= 4.25 = no
            # whippy candles) AND well-defined 15m trend (r2 >= 0.49 =
            # high linear-fit quality, structured move not noise).
            _trigger_clean_dip_trend_match = False
            _trigger_clean_dip_trend_reasons: list = []
            try:
                _cdt_dvr = (_chart_ctx_dict or {}).get("dip_volume_ratio") if isinstance(_chart_ctx_dict, dict) else None
                _cdt_wbm = _tier3_features.get("wick_body_5m_max") if isinstance(_tier3_features, dict) else None
                _cdt_r2 = (_chart_ctx_dict or {}).get("trend_15m_r_squared") if isinstance(_chart_ctx_dict, dict) else None
                if (_cdt_dvr is not None and float(_cdt_dvr) <= 0.88
                        and _cdt_wbm is not None and float(_cdt_wbm) <= 4.25
                        and _cdt_r2 is not None and float(_cdt_r2) >= 0.49):
                    _trigger_clean_dip_trend_match = True
                    _trigger_clean_dip_trend_reasons.append(
                        f"dip_vol_ratio={float(_cdt_dvr):.2f}<=0.88 AND "
                        f"wick_body_5m_max={float(_cdt_wbm):.2f}<=4.25 AND "
                        f"trend_15m_r2={float(_cdt_r2):.2f}>=0.49"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_clean_dip_trend err: {_e}")
            if _trigger_clean_dip_trend_match:
                logger.info(
                    f"[DipScanner] trigger_clean_dip_trend FIRED: {token_symbol} "
                    f"{';'.join(_trigger_clean_dip_trend_reasons)}"
                )

            # ── trigger_young_active_dip — ENFORCED 2026-05-17 ─────────
            # age_hours<=3.11 AND vol_h1>=261094 AND vol_m5>=13469.
            # Universe-recorder mining (n=2691, 24h):
            #   in-scope cohort: n=208, 79% WR5, +$978/day potential
            #   total cohort:    n=244, 79.9% WR5 (incl. 36 OOS rescues)
            # Signature: very young token (<3.11h) with established high
            # volume — usually means a launchpad graduate that's been
            # accumulating real activity. The 5m volume floor catches
            # tokens currently in active trading (not idle post-launch).
            #
            # Why not redundant with existing scanner: the bot's mcap/age
            # gates allow older + lower-activity tokens; this trigger
            # specifically promotes the young+active sub-slice that the
            # existing triggers don't enumerate (most existing triggers
            # gate on chart structure, not raw age+volume).
            #
            # No wash-trade guard — vol_h1 + vol_m5 floors already
            # establish real trading. Bot still applies mean_buy_size_usd
            # check at downstream entry.
            _trigger_young_active_dip_match = False
            _trigger_young_active_dip_reasons: list = []
            try:
                # 2026-05-17 PM — freshness precondition added after fluff
                # (8Hf1E...) crashed -67% combined h1+m5 yet vol_h1 looked good.
                # 1m_volume_spike >= 0.40 AND 1m_cum_3min_pct >= -3.0 confirms
                # token has REAL-TIME activity, not lagging-snapshot volume.
                _yad_vspike = m1_features.get("1m_volume_spike")
                _yad_cum3 = m1_features.get("1m_cum_3min_pct")
                _yad_fresh_ok = (
                    _yad_vspike is not None and float(_yad_vspike) >= 0.40
                    and _yad_cum3 is not None and float(_yad_cum3) >= -3.0
                )
                # 2026-05-18 — per-cohort entry-timing gate: age>=2.334h.
                # Mining (n=243): adding age>=2.334 lifts WR 79->91%, EV +2.3pp.
                # Very fresh (<2.3h) young_active_dip events are chaotic; the
                # 2.3-3.1h sweet spot has settled enough activity to time entry.
                if (
                    pair_age_hours >= 2.334
                    and pair_age_hours <= 3.11
                    and float(vol_h1 or 0) >= 261_094
                    and float(vol_m5 or 0) >= 13_469
                    and _yad_fresh_ok
                ):
                    _trigger_young_active_dip_match = True
                    _trigger_young_active_dip_reasons.append(
                        f"age={pair_age_hours:.2f}h in [2.334,3.11] AND "
                        f"vol_h1=${float(vol_h1 or 0):.0f}>=261k AND "
                        f"vol_m5=${float(vol_m5 or 0):.0f}>=13.5k AND "
                        f"1m_vol_spike={float(_yad_vspike or 0):.2f}>=0.40 AND "
                        f"1m_cum_3m={float(_yad_cum3 or 0):+.2f}%>=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_young_active_dip err: {_e}")
            if _trigger_young_active_dip_match:
                logger.info(
                    f"[DipScanner] trigger_young_active_dip FIRED: {token_symbol} "
                    f"{';'.join(_trigger_young_active_dip_reasons)}"
                )

            # ── trigger_volatile_5m_dip — ENFORCED 2026-05-17 PM ───────
            # range_pct_last >= 2.27 AND cum_5m_pct <= -10.43.
            # Universe-recorder mining (n=2691, 24h):
            #   n=658/day, 75% WR5, +9.06% rpnl/trade, $1192/day potential.
            # Pattern: volatile candle (high-low/low >= 2.27%) + 5min aggressive
            # dip (token down 10%+ over 5min). V-bottom with conviction signature.
            _trigger_volatile_5m_dip_match = False
            _trigger_volatile_5m_dip_reasons: list = []
            try:
                _v5d_rng = m1_features.get("1m_range_pct_last")
                _v5d_c5m = m1_features.get("1m_cum_5m_pct")
                # Freshness precondition: confirm bounce has started in last 3min,
                # not still actively crashing. Same gate as young_active_dip.
                _v5d_vspike = m1_features.get("1m_volume_spike")
                _v5d_cum3 = m1_features.get("1m_cum_3min_pct")
                _v5d_fresh_ok = (
                    _v5d_vspike is not None and float(_v5d_vspike) >= 0.40
                    and _v5d_cum3 is not None and float(_v5d_cum3) >= -3.0
                )
                # 2026-05-18 — per-cohort entry-timing gate: body_pct>=4.77.
                # Mining (n=658): adding strong-green-close gate lifts WR 73->84%,
                # EV +7.6pp. Wait for the reversal candle to be aggressive, not
                # just a mild green bar. Counter to v_bottom_body's body>=1.52.
                _v5d_body = m1_features.get("1m_last_close_pct")
                if (_v5d_rng is not None and float(_v5d_rng) >= 2.27
                        and _v5d_c5m is not None and float(_v5d_c5m) <= -10.43
                        and _v5d_body is not None and float(_v5d_body) >= 4.77
                        and _v5d_fresh_ok):
                    _trigger_volatile_5m_dip_match = True
                    _trigger_volatile_5m_dip_reasons.append(
                        f"range_pct={float(_v5d_rng):.2f}%>=2.27 AND "
                        f"cum_5m_pct={float(_v5d_c5m):+.2f}%<=-10.43 AND "
                        f"body={float(_v5d_body):+.2f}%>=4.77 AND "
                        f"1m_vol_spike={float(_v5d_vspike or 0):.2f}>=0.40 AND "
                        f"1m_cum_3m={float(_v5d_cum3 or 0):+.2f}%>=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_volatile_5m_dip err: {_e}")
            if _trigger_volatile_5m_dip_match:
                logger.info(
                    f"[DipScanner] trigger_volatile_5m_dip FIRED: {token_symbol} "
                    f"{';'.join(_trigger_volatile_5m_dip_reasons)}"
                )

            # ── trigger_v_bottom_body — ENFORCED 2026-05-17 PM (PREMIUM SIZE) ──
            # cum_5m_pct <= -10.43 AND last_close_pct >= 1.52
            # (= signed body of latest 1m candle is +1.52% or more green).
            # Universe-recorder mining (n=2691, 24h):
            #   n=176/day, 78.4% WR5, +10.18% rpnl/trade, $358/day potential.
            # Pattern: aggressive 5m dip + first 1m candle closes green +1.5%+.
            # This is the actual reversal candle — recovery has begun.
            # Premium sizing: this trigger gets 2x size ($40) due to high WR.
            _trigger_v_bottom_body_match = False
            _trigger_v_bottom_body_reasons: list = []
            try:
                _vbb_c5m = m1_features.get("1m_cum_5m_pct")
                _vbb_body = m1_features.get("1m_last_close_pct")
                # Freshness precondition (same as young_active_dip + volatile_5m_dip).
                # body>=1.52 already implies last 1m closed green, but adding the
                # vol_spike check ensures volume is alive on the bounce candle.
                _vbb_vspike = m1_features.get("1m_volume_spike")
                _vbb_fresh_ok = (
                    _vbb_vspike is not None and float(_vbb_vspike) >= 0.40
                )
                # 2026-05-18 — per-cohort entry-timing gate: age<=1.03h.
                # Mining (n=176): adding age<=1.03h lifts EV from +10.2% to +20.6%
                # (+10.3pp). V-bottoms on very fresh tokens have stronger recovery
                # than mature tokens — younger pools react more violently to
                # buy pressure on the reversal candle.
                if (pair_age_hours <= 1.028
                        and _vbb_c5m is not None and float(_vbb_c5m) <= -10.43
                        and _vbb_body is not None and float(_vbb_body) >= 1.52
                        and _vbb_fresh_ok):
                    _trigger_v_bottom_body_match = True
                    _trigger_v_bottom_body_reasons.append(
                        f"age={pair_age_hours:.2f}h<=1.03 AND "
                        f"cum_5m_pct={float(_vbb_c5m):+.2f}%<=-10.43 AND "
                        f"body={float(_vbb_body):+.2f}%>=1.52 AND "
                        f"1m_vol_spike={float(_vbb_vspike or 0):.2f}>=0.40"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_v_bottom_body err: {_e}")
            if _trigger_v_bottom_body_match:
                logger.info(
                    f"[DipScanner] trigger_v_bottom_body FIRED (PREMIUM SIZE): "
                    f"{token_symbol} {';'.join(_trigger_v_bottom_body_reasons)}"
                )

            # ── trigger_volume_burst_runner — ENFORCED 2026-05-17 PM ─────
            # vol_at_event >= 1000 AND age <= 24h AND liq_usd <= $50k.
            # + freshness (1m_vol_spike >= 0.40 AND 1m_cum_3min_pct >= -3.0).
            # Universe-recorder mining (n=2691, 24h):
            #   n=408/day, 80.1% WR5, +10.78% rpnl/trade, $879/day potential.
            # Pattern: volume burst hitting a young (<24h) low-liq (<$50k) token —
            # the classic +50-100% runner signature emerging from a small pool.
            _trigger_volume_burst_runner_match = False
            _trigger_volume_burst_runner_reasons: list = []
            try:
                _vbr_vol_at = float(last.volume or 0) if last is not None else 0.0
                _vbr_vspike = m1_features.get("1m_volume_spike")
                _vbr_cum3 = m1_features.get("1m_cum_3min_pct")
                _vbr_fresh_ok = (
                    _vbr_vspike is not None and float(_vbr_vspike) >= 0.40
                    and _vbr_cum3 is not None and float(_vbr_cum3) >= -3.0
                )
                # 2026-05-18 — per-cohort entry-timing gate: liq<=$21k.
                # Mining (n=408): tightening liq cap from $50k to $21k lifts
                # WR 78->85%, EV +9.6pp. Smaller pools (<$21k) react more
                # violently to volume bursts; $21k-$50k tier dilutes the signal.
                if (
                    _vbr_vol_at >= 1000.0
                    and pair_age_hours <= 24.0
                    and liq_usd <= 21_290
                    and _vbr_fresh_ok
                ):
                    _trigger_volume_burst_runner_match = True
                    _trigger_volume_burst_runner_reasons.append(
                        f"vol_at_event=${_vbr_vol_at:.0f}>=1000 AND "
                        f"age={pair_age_hours:.1f}h<=24 AND "
                        f"liq=${liq_usd:.0f}<=21.3k AND "
                        f"1m_vol_spike={float(_vbr_vspike or 0):.2f}>=0.40 AND "
                        f"1m_cum_3m={float(_vbr_cum3 or 0):+.2f}%>=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_volume_burst_runner err: {_e}")
            if _trigger_volume_burst_runner_match:
                logger.info(
                    f"[DipScanner] trigger_volume_burst_runner FIRED: {token_symbol} "
                    f"{';'.join(_trigger_volume_burst_runner_reasons)}"
                )

            # ── trigger_volatile_buyer_dom — ENFORCED 2026-05-17 PM ──────
            # range_pct_last >= 3 AND bs_h6 >= 2 AND age <= 12h + freshness.
            # Universe-recorder mining (n=2691, 24h):
            #   n=115/day, 82.6% WR5, +8.16% rpnl/trade, $188/day potential.
            # Pattern: volatile candle + 6h buyer dominance + young token.
            # New dimension: bs_h6 (6h buy-to-sell ratio) not used elsewhere.
            _trigger_volatile_buyer_dom_match = False
            _trigger_volatile_buyer_dom_reasons: list = []
            try:
                _vbd_rng = m1_features.get("1m_range_pct_last")
                _vbd_vspike = m1_features.get("1m_volume_spike")
                _vbd_cum3 = m1_features.get("1m_cum_3min_pct")
                _vbd_fresh_ok = (
                    _vbd_vspike is not None and float(_vbd_vspike) >= 0.40
                    and _vbd_cum3 is not None and float(_vbd_cum3) >= -3.0
                )
                # 2026-05-18 — per-cohort entry-timing gate: age>=0.46h.
                # Mining (n=115): excluding very-fresh (<28min) tokens lifts
                # WR 82->84%, marginal EV +0.6pp but ~free (97% throughput retained).
                if (
                    pair_age_hours >= 0.46
                    and pair_age_hours <= 12.0
                    and _vbd_rng is not None and float(_vbd_rng) >= 3.0
                    and ratio_h6 >= 2.0
                    and _vbd_fresh_ok
                ):
                    _trigger_volatile_buyer_dom_match = True
                    _trigger_volatile_buyer_dom_reasons.append(
                        f"range_pct={float(_vbd_rng):.2f}%>=3 AND "
                        f"bs_h6={ratio_h6:.2f}>=2 AND "
                        f"age={pair_age_hours:.2f}h in [0.46,12] AND "
                        f"1m_vol_spike={float(_vbd_vspike or 0):.2f}>=0.40"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_volatile_buyer_dom err: {_e}")
            if _trigger_volatile_buyer_dom_match:
                logger.info(
                    f"[DipScanner] trigger_volatile_buyer_dom FIRED: {token_symbol} "
                    f"{';'.join(_trigger_volatile_buyer_dom_reasons)}"
                )

            # ── trigger_fresh_runner_factory — ENFORCED 2026-05-17 PM (3x PREMIUM) ──
            # age <= 0.95h AND vol_h1 >= 261k AND 1m_vol_prev3_avg >= 4057
            # + freshness (1m_vol_spike >= 0.40 AND 1m_cum_3min_pct >= -3.0).
            # ELITE runner predictor. Universe-recorder mining (n=2691, 24h):
            #   n=71/day, 69% P(peak>=20%), 44% P(peak>=50%), +59.6% avg peak.
            # Pattern: brand-new tokens (<1h) with established 1h volume AND
            # 3-bar volume momentum — the runner factory. Premium-premium size
            # (3x = $60) due to expected +18%/trade rpnl with asymmetric exit.
            _trigger_fresh_runner_factory_match = False
            _trigger_fresh_runner_factory_reasons: list = []
            try:
                _frf_vol_prev3 = m1_features.get("1m_vol_prev3_avg")
                _frf_vol_prev15 = m1_features.get("1m_vol_prev15_avg")
                _frf_vspike = m1_features.get("1m_volume_spike")
                _frf_cum3 = m1_features.get("1m_cum_3min_pct")
                _frf_fresh_ok = (
                    _frf_vspike is not None and float(_frf_vspike) >= 0.40
                    and _frf_cum3 is not None and float(_frf_cum3) >= -3.0
                )
                # 2026-05-18 — per-cohort entry-timing gate: vol_accel>=1.085.
                # vol_accel = vol_prev3 / vol_prev15 (last 3min vol vs last 15min avg).
                # Mining (n=70): adding vol_accel>=1.085 lifts WR 83->94%, EV +14.4pp.
                # The BIGGEST timing signal in the data. Buy when real-time volume
                # is GENUINELY accelerating (last 3 min > 15-min avg), not when
                # volume just LOOKS active from h1 metrics.
                _frf_vol_accel = (
                    float(_frf_vol_prev3) / float(_frf_vol_prev15)
                    if (_frf_vol_prev3 is not None and _frf_vol_prev15 is not None
                        and float(_frf_vol_prev15) > 0)
                    else None
                )
                if (
                    pair_age_hours <= 0.95
                    and float(vol_h1 or 0) >= 261_094
                    and _frf_vol_prev3 is not None and float(_frf_vol_prev3) >= 4057.0
                    and _frf_vol_accel is not None and _frf_vol_accel >= 1.085
                    and _frf_fresh_ok
                ):
                    _trigger_fresh_runner_factory_match = True
                    _trigger_fresh_runner_factory_reasons.append(
                        f"age={pair_age_hours:.2f}h<=0.95 AND "
                        f"vol_h1=${float(vol_h1 or 0):.0f}>=261k AND "
                        f"vol_prev3=${float(_frf_vol_prev3):.0f}>=4057 AND "
                        f"vol_accel={_frf_vol_accel:.3f}>=1.085 AND "
                        f"1m_vol_spike={float(_frf_vspike or 0):.2f}>=0.40 AND "
                        f"1m_cum_3m={float(_frf_cum3 or 0):+.2f}%>=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_fresh_runner_factory err: {_e}")
            if _trigger_fresh_runner_factory_match:
                logger.warning(
                    f"[DipScanner] 🚀 trigger_fresh_runner_factory FIRED (3x PREMIUM): "
                    f"{token_symbol} {';'.join(_trigger_fresh_runner_factory_reasons)}"
                )

            # ──────────────────────────────────────────────────────────────
            # 2026-05-18 — Trending-token flag. Axiom Top+Trending tabs
            # stamp the shared tracker every fetch (5-min cadence). Tokens
            # currently on the trending list get is_trending_token=True
            # in entry_meta. Restores the April high-WR era's "ride hot
            # tokens of the moment" specialization signal without changing
            # the trigger architecture — informational only for now.
            try:
                from feeds.trending_tracker import is_trending as _is_t
                _is_trending_token = bool(_is_t(token_address))
            except Exception:
                _is_trending_token = False

            # ──────────────────────────────────────────────────────────────
            # 2026-05-18 ROUND-5 TRIGGERS — universe-recorder volume push.
            # Greedy stacking pushed projected coverage from 221 to 1250
            # events/day (76% WR, +7.70% avg pnl, +$1926/day potential).
            # All four require the 1m freshness gate (vol_spike >= 0.40 AND
            # cum_3min >= -3) per feedback_lagging_features_freshness.
            # ──────────────────────────────────────────────────────────────
            _txns_h1 = (pair.get("txns") or {}).get("h1", {}) or {}
            _buys_h1 = float(_txns_h1.get("buys", 0) or 0)
            _sells_h1 = float(_txns_h1.get("sells", 0) or 0)
            _r5_vspike = m1_features.get("1m_volume_spike")
            _r5_cum3 = m1_features.get("1m_cum_3min_pct")
            _r5_fresh_ok = (
                _r5_vspike is not None and float(_r5_vspike) >= 0.40
                and _r5_cum3 is not None and float(_r5_cum3) >= -3.0
            )

            # ── trigger_active_dip — ENFORCED 2026-05-18 ─────────────────
            # buys_h1 >= 200 AND pc_h1 <= -15. Mining: n=673/day, 76% WR5,
            # +7.05% rpnl, $949/day potential. Pattern: token sharply dipped
            # (>=15% h1 drop) but sustained buyer activity (>=200 buys/h1) =
            # real dip-buy opportunity, not a corpse.
            _trigger_active_dip_match = False
            _trigger_active_dip_reasons: list = []
            try:
                if (_buys_h1 >= 200 and pc_h1 <= -15 and _r5_fresh_ok):
                    _trigger_active_dip_match = True
                    _trigger_active_dip_reasons.append(
                        f"buys_h1={_buys_h1:.0f}>=200 AND pc_h1={pc_h1:+.1f}%<=-15 AND "
                        f"1m_vol_spike={float(_r5_vspike or 0):.2f}>=0.40 AND "
                        f"1m_cum_3m={float(_r5_cum3 or 0):+.2f}%>=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_active_dip err: {_e}")
            if _trigger_active_dip_match:
                logger.info(
                    f"[DipScanner] trigger_active_dip FIRED: {token_symbol} "
                    f"{';'.join(_trigger_active_dip_reasons)}"
                )

            # ── trigger_high_activity_runner — ENFORCED 2026-05-18 ───────
            # vol_h1 >= ~$31.6k (log_vol_h1>=4.5) AND buys_h1 >= 2000.
            # Mining: n=654/day, 77.5% WR5, +10.20% rpnl, $1334/day potential.
            # Pattern: established activity + frenzied buying = real momentum.
            _trigger_high_activity_runner_match = False
            _trigger_high_activity_runner_reasons: list = []
            try:
                if (float(vol_h1 or 0) >= 31_623 and _buys_h1 >= 2000 and _r5_fresh_ok):
                    _trigger_high_activity_runner_match = True
                    _trigger_high_activity_runner_reasons.append(
                        f"vol_h1=${float(vol_h1 or 0):.0f}>=31.6k AND "
                        f"buys_h1={_buys_h1:.0f}>=2000 AND "
                        f"1m_vol_spike={float(_r5_vspike or 0):.2f}>=0.40 AND "
                        f"1m_cum_3m={float(_r5_cum3 or 0):+.2f}%>=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_high_activity_runner err: {_e}")
            if _trigger_high_activity_runner_match:
                logger.info(
                    f"[DipScanner] trigger_high_activity_runner FIRED: {token_symbol} "
                    f"{';'.join(_trigger_high_activity_runner_reasons)}"
                )

            # ── trigger_confirmed_dip — ENFORCED 2026-05-18 ──────────────
            # pc_m5 <= -5 AND pc_h1 <= -15. Mining: n=579/day, 76.7% WR5,
            # +7.13% rpnl, $826/day potential. Two-timeframe dip confirmation
            # (both 5m and 1h sharply negative). Freshness gate critical here
            # — without it, the pattern matches dying tokens.
            _trigger_confirmed_dip_match = False
            _trigger_confirmed_dip_reasons: list = []
            try:
                if (pc_m5 <= -5 and pc_h1 <= -15 and _r5_fresh_ok):
                    _trigger_confirmed_dip_match = True
                    _trigger_confirmed_dip_reasons.append(
                        f"pc_m5={pc_m5:+.1f}%<=-5 AND pc_h1={pc_h1:+.1f}%<=-15 AND "
                        f"1m_vol_spike={float(_r5_vspike or 0):.2f}>=0.40 AND "
                        f"1m_cum_3m={float(_r5_cum3 or 0):+.2f}%>=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_confirmed_dip err: {_e}")
            if _trigger_confirmed_dip_match:
                logger.info(
                    f"[DipScanner] trigger_confirmed_dip FIRED: {token_symbol} "
                    f"{';'.join(_trigger_confirmed_dip_reasons)}"
                )

            # ── trigger_low_liq_active_dip — ENFORCED 2026-05-18 ─────────
            # buys_h1 >= 1000 AND liq <= $30k AND range_pct >= 1. Mining:
            # n=330/day, 80.6% WR5, +11.27% rpnl, $744/day potential.
            # Pattern: small pool (<$30k liq) + active buying (1k buys/h1)
            # + any volatility (range_pct>=1) = explosive small-cap setup.
            _trigger_low_liq_active_dip_match = False
            _trigger_low_liq_active_dip_reasons: list = []
            try:
                _lla_rng = m1_features.get("1m_range_pct_last")
                if (_buys_h1 >= 1000
                        and liq_usd <= 30_000
                        and _lla_rng is not None and float(_lla_rng) >= 1.0
                        and _r5_fresh_ok):
                    _trigger_low_liq_active_dip_match = True
                    _trigger_low_liq_active_dip_reasons.append(
                        f"buys_h1={_buys_h1:.0f}>=1000 AND "
                        f"liq=${liq_usd:.0f}<=30k AND "
                        f"range_pct={float(_lla_rng):.2f}%>=1 AND "
                        f"1m_vol_spike={float(_r5_vspike or 0):.2f}>=0.40 AND "
                        f"1m_cum_3m={float(_r5_cum3 or 0):+.2f}%>=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_low_liq_active_dip err: {_e}")
            if _trigger_low_liq_active_dip_match:
                logger.info(
                    f"[DipScanner] trigger_low_liq_active_dip FIRED: {token_symbol} "
                    f"{';'.join(_trigger_low_liq_active_dip_reasons)}"
                )

            # ── trigger_high_churn_microcap — ENFORCED 2026-05-18 ────────
            # vol_h1/mcap >= 0.25 AND vol_h6/liq >= 10 AND age <= 6h.
            # Mining round-6: n=60/day (uncovered after R5 stack), 78.3% WR5,
            # +6.15% rpnl, +$74/day. Pattern: high-churn microcap where the
            # market is actively trading >25% of mcap per hour AND vol is
            # 10x+ liquidity (deep churn). Young (<6h) tokens with this
            # signature are explosive.
            _trigger_high_churn_microcap_match = False
            _trigger_high_churn_microcap_reasons: list = []
            try:
                _hcm_vol_h6 = (pair.get("volume") or {}).get("h6", 0) or 0
                _hcm_vol_mcap = (float(vol_h1 or 0) / float(mcap)) if mcap and float(mcap) > 0 else 0
                _hcm_liq_vel_h6 = (float(_hcm_vol_h6) / float(liq_usd)) if liq_usd and liq_usd > 0 else 0
                if (
                    _hcm_vol_mcap >= 0.25
                    and _hcm_liq_vel_h6 >= 10.0
                    and pair_age_hours <= 6.0
                    and _r5_fresh_ok
                ):
                    _trigger_high_churn_microcap_match = True
                    _trigger_high_churn_microcap_reasons.append(
                        f"vol_h1/mcap={_hcm_vol_mcap:.2f}>=0.25 AND "
                        f"vol_h6/liq={_hcm_liq_vel_h6:.1f}>=10 AND "
                        f"age={pair_age_hours:.2f}h<=6 AND "
                        f"1m_vol_spike={float(_r5_vspike or 0):.2f}>=0.40 AND "
                        f"1m_cum_3m={float(_r5_cum3 or 0):+.2f}%>=-3"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_high_churn_microcap err: {_e}")
            if _trigger_high_churn_microcap_match:
                logger.info(
                    f"[DipScanner] trigger_high_churn_microcap FIRED: {token_symbol} "
                    f"{';'.join(_trigger_high_churn_microcap_reasons)}"
                )

            # ── trigger_micro_pattern_confirmed — ENFORCED 2026-05-15 ──
            # Textbook technical-pattern detection (bull engulfing, double
            # bottom, inverse H&S, falling wedge, long lower wick, etc.)
            # combined with flow + mtf alignment. Lifetime: 10/11 wins
            # (90.9% WR), +$5.34 net. LARGEST-n among the new triggers.
            _trigger_micro_pattern_match = False
            _trigger_micro_pattern_reasons: list = []
            try:
                _nf60_mp = _tier3_features.get("net_flow_60s_usd") if isinstance(_tier3_features, dict) else None
                _mtf_mp = (_chart_ctx_dict or {}).get("chart_mtf_score") if isinstance(_chart_ctx_dict, dict) else None
                _mps = (_chart_ctx_dict or {}).get("micro_pattern_score") if isinstance(_chart_ctx_dict, dict) else None
                if (_nf60_mp is not None and float(_nf60_mp) > 0
                        and _mtf_mp is not None and float(_mtf_mp) >= 1
                        and _mps is not None and float(_mps) > 0):
                    _trigger_micro_pattern_match = True
                    _trigger_micro_pattern_reasons.append(
                        f"net_flow_60s=${float(_nf60_mp):+.0f}>0 AND "
                        f"mtf={float(_mtf_mp):.1f}>=1 AND "
                        f"micro_pattern_score={float(_mps):.1f}>0"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_micro_pattern err: {_e}")
            if _trigger_micro_pattern_match:
                logger.info(
                    f"[DipScanner] trigger_micro_pattern_confirmed FIRED: {token_symbol} "
                    f"{';'.join(_trigger_micro_pattern_reasons)}"
                )

            # ── trigger_volume_profile_aligned — ENFORCED 2026-05-15 ───
            # Volume-profile anchored entry: price near POC (high-volume
            # node, |dist| < 20%) + flow > $50 + mtf>=1. POC is where the
            # most trading occurred historically — entries here are at
            # value, not at extension. Lifetime: 8/8 wins (100% WR), +$4.67.
            _trigger_vp_aligned_match = False
            _trigger_vp_aligned_reasons: list = []
            try:
                _nf60_vp = _tier3_features.get("net_flow_60s_usd") if isinstance(_tier3_features, dict) else None
                _mtf_vp = (_chart_ctx_dict or {}).get("chart_mtf_score") if isinstance(_chart_ctx_dict, dict) else None
                _vp_dist = (_chart_ctx_dict or {}).get("chart_vp_poc_distance_pct") if isinstance(_chart_ctx_dict, dict) else None
                if (_nf60_vp is not None and float(_nf60_vp) > 50
                        and _mtf_vp is not None and float(_mtf_vp) >= 1
                        and _vp_dist is not None and -20 < float(_vp_dist) < 20):
                    _trigger_vp_aligned_match = True
                    _trigger_vp_aligned_reasons.append(
                        f"net_flow_60s=${float(_nf60_vp):+.0f}>50 AND "
                        f"mtf={float(_mtf_vp):.1f}>=1 AND "
                        f"vp_poc_dist={float(_vp_dist):+.1f}% in (-20,20)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_vp_aligned err: {_e}")
            if _trigger_vp_aligned_match:
                logger.info(
                    f"[DipScanner] trigger_volume_profile_aligned FIRED: {token_symbol} "
                    f"{';'.join(_trigger_vp_aligned_reasons)}"
                )

            # ── trigger_quiet_1s_buyer_dominance — ENFORCED 2026-05-15 ─
            # Strong flow + sustained buyer dominance on h6 + quiet 1s
            # tape (red_pct<0.5 means majority green bars last 60s).
            # Lifetime: 10/12 wins (83.3% WR), +$4.46 net. LARGEST-n high-
            # WR compound found. Uses 1s_red_pct dimension not in any
            # other trigger.
            _trigger_quiet_buyer_match = False
            _trigger_quiet_buyer_reasons: list = []
            try:
                _nf60_qb = _tier3_features.get("net_flow_60s_usd") if isinstance(_tier3_features, dict) else None
                _bs_h6_qb = float(ratio_h6) if ratio_h6 != float("inf") else None
                _1s_redp = _1s_features.get("red_pct_60s") if isinstance(_1s_features, dict) else None
                if (_nf60_qb is not None and float(_nf60_qb) > 50
                        and _bs_h6_qb is not None and _bs_h6_qb >= 1.2
                        and _1s_redp is not None and 0 <= float(_1s_redp) < 0.5):
                    _trigger_quiet_buyer_match = True
                    _trigger_quiet_buyer_reasons.append(
                        f"net_flow_60s=${float(_nf60_qb):+.0f}>50 AND "
                        f"bs_h6={_bs_h6_qb:.2f}>=1.2 AND "
                        f"1s_red_pct={float(_1s_redp):.2f}<0.5"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_quiet_buyer err: {_e}")
            if _trigger_quiet_buyer_match:
                logger.info(
                    f"[DipScanner] trigger_quiet_1s_buyer_dominance FIRED: {token_symbol} "
                    f"{';'.join(_trigger_quiet_buyer_reasons)}"
                )

            # ── trigger_vp_poc_orderflow_bounce — ENFORCED 2026-05-16 (R3) ─
            # Volume-profile-POC proximity + strong real-time flow + bullish
            # 1s tape + size floor. Round-3 mining cohort: 11/11 lifetime
            # wins, +5.57% avg (highest-n 100% WR compound in round 3).
            # Self-gated wash-resistant (mean_buy>=15 baked in).
            _trigger_vp_orderflow_match = False
            _trigger_vp_orderflow_reasons: list = []
            try:
                _mbs_vp = (_tier1_features.get("mean_buy_size_usd")
                           if isinstance(_tier1_features, dict) else None)
                if _mbs_vp is None and isinstance(_tier3_features, dict):
                    _mbs_vp = _tier3_features.get("mean_buy_size_usd")
                _nf60_vp = _tier3_features.get("net_flow_60s_usd") if isinstance(_tier3_features, dict) else None
                _vpd_vp = (_chart_ctx_dict or {}).get("chart_vp_poc_distance_pct") if isinstance(_chart_ctx_dict, dict) else None
                _1s_cp_vp = _1s_features.get("close_pos_60s") if isinstance(_1s_features, dict) else None
                _bsm5_vp = float(ratio_m5) if ratio_m5 != float("inf") else None
                if (_mbs_vp is not None and float(_mbs_vp) >= 15
                        and _nf60_vp is not None and float(_nf60_vp) > 50
                        and _vpd_vp is not None and abs(float(_vpd_vp)) < 20
                        and _1s_cp_vp is not None and float(_1s_cp_vp) > 0.5
                        and _bsm5_vp is not None and _bsm5_vp >= 1.5):
                    _trigger_vp_orderflow_match = True
                    _trigger_vp_orderflow_reasons.append(
                        f"mean_buy=${float(_mbs_vp):.0f}>=15 AND "
                        f"net_flow_60s=${float(_nf60_vp):+.0f}>50 AND "
                        f"vp_poc_dist={float(_vpd_vp):+.1f}%<20 AND "
                        f"1s_close_pos={float(_1s_cp_vp):.2f}>0.5 AND "
                        f"bs_m5={_bsm5_vp:.2f}>=1.5"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_vp_orderflow err: {_e}")
            if _trigger_vp_orderflow_match:
                logger.info(
                    f"[DipScanner] trigger_vp_poc_orderflow_bounce FIRED: {token_symbol} "
                    f"{';'.join(_trigger_vp_orderflow_reasons)}"
                )

            # ── trigger_reaccum_vol_bounce — ENFORCED 2026-05-16 (R3) ──────
            # Reaccumulation-volume signature + MTF alignment + 1s tape
            # bullish + size floor. Uses chart_reaccum_vol_return_ratio>1
            # (volume returning to pre-trough levels — accumulation re-
            # engaging). Round-3 mining: 7/7 lifetime wins, +5.09% avg.
            # Structurally NEW dimension not in any prior trigger.
            _trigger_reaccum_vol_match = False
            _trigger_reaccum_vol_reasons: list = []
            try:
                _mbs_rv = (_tier1_features.get("mean_buy_size_usd")
                           if isinstance(_tier1_features, dict) else None)
                if _mbs_rv is None and isinstance(_tier3_features, dict):
                    _mbs_rv = _tier3_features.get("mean_buy_size_usd")
                _mtf_rv = (_chart_ctx_dict or {}).get("chart_mtf_score") if isinstance(_chart_ctx_dict, dict) else None
                _rv_ratio = (_chart_ctx_dict or {}).get("chart_reaccum_vol_return_ratio") if isinstance(_chart_ctx_dict, dict) else None
                _1s_cp_rv = _1s_features.get("close_pos_60s") if isinstance(_1s_features, dict) else None
                if (_mbs_rv is not None and float(_mbs_rv) >= 15
                        and _mtf_rv is not None and float(_mtf_rv) >= 1
                        and _rv_ratio is not None and float(_rv_ratio) > 1.0
                        and _1s_cp_rv is not None and float(_1s_cp_rv) > 0.5):
                    _trigger_reaccum_vol_match = True
                    _trigger_reaccum_vol_reasons.append(
                        f"mean_buy=${float(_mbs_rv):.0f}>=15 AND "
                        f"mtf={float(_mtf_rv):.1f}>=1 AND "
                        f"reaccum_vol_ratio={float(_rv_ratio):.2f}>1.0 AND "
                        f"1s_close_pos={float(_1s_cp_rv):.2f}>0.5"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_reaccum_vol err: {_e}")
            if _trigger_reaccum_vol_match:
                logger.info(
                    f"[DipScanner] trigger_reaccum_vol_bounce FIRED: {token_symbol} "
                    f"{';'.join(_trigger_reaccum_vol_reasons)}"
                )

            # ── trigger_tight_buyer_mtf — ENFORCED 2026-05-16 (R3) ─────────
            # MTF alignment + tight 1s tape (close_pos>0.6) + buyer
            # dominance + size floor. Tighter 1s_close_pos threshold than
            # other triggers captures only confirmed bullish-close tape.
            # Round-3 mining: 8/8 lifetime wins, +5.33% avg.
            _trigger_tight_buyer_mtf_match = False
            _trigger_tight_buyer_mtf_reasons: list = []
            try:
                _mbs_tb = (_tier1_features.get("mean_buy_size_usd")
                           if isinstance(_tier1_features, dict) else None)
                if _mbs_tb is None and isinstance(_tier3_features, dict):
                    _mbs_tb = _tier3_features.get("mean_buy_size_usd")
                _mtf_tb = (_chart_ctx_dict or {}).get("chart_mtf_score") if isinstance(_chart_ctx_dict, dict) else None
                _1s_cp_tb = _1s_features.get("close_pos_60s") if isinstance(_1s_features, dict) else None
                _bsm5_tb = float(ratio_m5) if ratio_m5 != float("inf") else None
                if (_mbs_tb is not None and float(_mbs_tb) >= 15
                        and _mtf_tb is not None and float(_mtf_tb) >= 1
                        and _1s_cp_tb is not None and float(_1s_cp_tb) > 0.6
                        and _bsm5_tb is not None and _bsm5_tb >= 1.5):
                    _trigger_tight_buyer_mtf_match = True
                    _trigger_tight_buyer_mtf_reasons.append(
                        f"mean_buy=${float(_mbs_tb):.0f}>=15 AND "
                        f"mtf={float(_mtf_tb):.1f}>=1 AND "
                        f"1s_close_pos={float(_1s_cp_tb):.2f}>0.6 AND "
                        f"bs_m5={_bsm5_tb:.2f}>=1.5"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] trigger_tight_buyer_mtf err: {_e}")
            if _trigger_tight_buyer_mtf_match:
                logger.info(
                    f"[DipScanner] trigger_tight_buyer_mtf FIRED: {token_symbol} "
                    f"{';'.join(_trigger_tight_buyer_mtf_reasons)}"
                )

            # ── ANTI-PATTERN: extended-uptrend-into-entry suppression ──
            # Inverse mining surfaced TWO 0/7 loss cohorts that share a
            # signature: token has been making higher lows AND is mid-
            # extension at entry time. ALL such trades lost lifetime.
            #
            # Suppression rule: if (mtf_textbook_pullback AND hl_delta>0)
            # OR (hl_delta>0 AND trend_30m_consec_hh>=1), CLEAR all
            # triggers. Saves ~$27 of historical losses while having
            # zero impact on winners (no winner satisfies either cohort).
            #
            # This is NOT a new filter (doesn't add to the upstream wall).
            # It's a TRIGGER-LEVEL suppression that only fires when we
            # have a trigger AND a loss-cohort signature. Surgical.
            _suppress_reason = None
            try:
                _hl_delta = (entry_meta_dict.get("hl_delta_pct") if False else None)
                # entry_meta_dict not yet built; use existing locals
                _hl_d = None
                _mtf_tp = None
                _tr_hh = None
                # Pull from chart context features if available
                _hl_d = (_chart_ctx_dict or {}).get("hl_delta_pct") if isinstance(_chart_ctx_dict, dict) else None
                _mtf_tp = (_chart_ctx_dict or {}).get("mtf_textbook_pullback") if isinstance(_chart_ctx_dict, dict) else None
                _tr_hh = (_chart_ctx_dict or {}).get("trend_30m_consec_hh") if isinstance(_chart_ctx_dict, dict) else None
                if _hl_d is not None and float(_hl_d) > 0:
                    if _mtf_tp is not None and float(_mtf_tp) >= 1:
                        _suppress_reason = (
                            f"mtf_textbook_pullback AND hl_delta_pct={float(_hl_d):.2f}>0 "
                            f"(0/7 lifetime WR cohort)"
                        )
                    elif _tr_hh is not None and float(_tr_hh) >= 1:
                        _suppress_reason = (
                            f"trend_30m_consec_hh>=1 AND hl_delta_pct={float(_hl_d):.2f}>0 "
                            f"(0/7 lifetime WR cohort)"
                        )
            except Exception:
                _suppress_reason = None

            # ── WASH-TRADING GUARD (2026-05-16) ──────────────────────────
            # All 9 new on-chain triggers use bs_m5/h1/h6 (txn COUNT ratios)
            # and net_flow_60s_usd which can be skewed by micro-buys ($0.05
            # each) that inflate count without real $ flow. BULLISH entry
            # at 00:44 UTC had mean_buy_size_usd=$0.52 — pure wash signature
            # that all 9 triggers were vulnerable to.
            #
            # Guard: require mean_buy_size_usd >= $10 for ANY new on-chain
            # trigger to fire. Lifetime evidence: WIN avg $84, LOSE avg $54
            # — $10 floor is well below both distributions but cleanly
            # excludes wash (avg <$1 typical).
            _mean_buy_size = (entry_meta_dict.get("mean_buy_size_usd")
                              if False else None)  # placeholder; meta not built yet
            try:
                _mbs = (_tier1_features.get("mean_buy_size_usd")
                        if isinstance(_tier1_features, dict) else None)
                if _mbs is None and isinstance(_tier3_features, dict):
                    _mbs = _tier3_features.get("mean_buy_size_usd")
            except Exception:
                _mbs = None
            _wash_guard_block = False
            if _mbs is not None and float(_mbs) < 10:
                _wash_guard_block = True
                logger.info(
                    f"[DipScanner] new-triggers WASH-GUARDED: {token_symbol} "
                    f"mean_buy_size=${float(_mbs):.2f}<$10 (wash-trade signature; "
                    f"clearing 9 new on-chain triggers)"
                )
                c["new_trigger_wash_guarded"] = c.get("new_trigger_wash_guarded", 0) + 1
                _trigger_strong_orderflow_match = False
                _trigger_sustained_accum_match = False
                _trigger_chart_qual_bottom_match = False
                _trigger_buyer_momentum_burst_match = False
                _trigger_flow_reversal_match = False
                _trigger_chart_reversal_match = False
                _trigger_micro_pattern_match = False
                _trigger_vp_aligned_match = False
                _trigger_quiet_buyer_match = False
                # R3 (2026-05-16) triggers self-gate on mean_buy>=15 but
                # clear here defensively for consistency.
                _trigger_vp_orderflow_match = False
                _trigger_reaccum_vol_match = False
                _trigger_tight_buyer_mtf_match = False

            # Determine effective entry decision: enter if ANY trigger fires
            _triggers_fired = []
            if _trigger_strong_orderflow_match:
                _triggers_fired.append("strong_orderflow")
            if _trigger_sustained_accum_match:
                _triggers_fired.append("sustained_accumulation")
            if _trigger_chart_qual_bottom_match:
                _triggers_fired.append("chart_quality_bottom")
            if _trigger_buyer_momentum_burst_match:
                _triggers_fired.append("buyer_momentum_burst")
            if _trigger_flow_reversal_match:
                _triggers_fired.append("flow_reversal")
            if _trigger_chart_reversal_match:
                _triggers_fired.append("chart_score_reversal")
            if _trigger_micro_pattern_match:
                _triggers_fired.append("micro_pattern_confirmed")
            if _trigger_vp_aligned_match:
                _triggers_fired.append("volume_profile_aligned")
            if _trigger_quiet_buyer_match:
                _triggers_fired.append("quiet_1s_buyer_dominance")
            # R3 (2026-05-16) wash-resistant triggers.
            if _trigger_vp_orderflow_match:
                _triggers_fired.append("vp_poc_orderflow_bounce")
            if _trigger_reaccum_vol_match:
                _triggers_fired.append("reaccum_vol_bounce")
            if _trigger_tight_buyer_mtf_match:
                _triggers_fired.append("tight_buyer_mtf")
            # R2 (2026-05-17) round-2 mining triggers.
            if _trigger_swing_structure_rsi_match:
                _triggers_fired.append("swing_structure_rsi")
            if _trigger_channel_pos_swing_match:
                _triggers_fired.append("channel_pos_swing")
            # R3 (2026-05-17) round-3 mining triggers.
            if _trigger_channel_hvn_match:
                _triggers_fired.append("channel_hvn")
            if _trigger_shape_wick_match:
                _triggers_fired.append("shape_wick")
            if _trigger_cnn_lp_match:
                _triggers_fired.append("cnn_lp")
            # R4 (2026-05-17) round-4 mining triggers.
            if _trigger_clean_consec_ll_match:
                _triggers_fired.append("clean_consec_ll")
            # R5 (2026-05-17) round-5 mining trigger.
            # DISABLED 2026-05-17 (bug fix): top10_holder_pct is not available
            # at scan time (post-rugcheck only). Trigger can never match in
            # production. Predicate computation kept above for entry_meta
            # stamping in case data plumbing changes. Do NOT append to
            # _triggers_fired and do NOT include in breakthrough_late_match.
            # if _trigger_sweep_holder_liq_match:
            #     _triggers_fired.append("sweep_holder_liq")
            # R6 (2026-05-17) round-6 mining trigger.
            if _trigger_clean_dip_trend_match:
                _triggers_fired.append("clean_dip_trend")
            # 2026-05-17 — young_active_dip from universe-recorder mining.
            if _trigger_young_active_dip_match:
                _triggers_fired.append("young_active_dip")
            # 2026-05-17 PM — V-bottom triggers from universe-recorder mining.
            if _trigger_volatile_5m_dip_match:
                _triggers_fired.append("volatile_5m_dip")
            if _trigger_v_bottom_body_match:
                _triggers_fired.append("v_bottom_body")
            # 2026-05-17 PM — round-2 deep-mine triggers (post-freshness fix).
            if _trigger_volume_burst_runner_match:
                _triggers_fired.append("volume_burst_runner")
            if _trigger_volatile_buyer_dom_match:
                _triggers_fired.append("volatile_buyer_dom")
            # 2026-05-17 PM — runner-predictive mining (3x PREMIUM).
            if _trigger_fresh_runner_factory_match:
                _triggers_fired.append("fresh_runner_factory")
            # 2026-05-18 — Round-5 volume-push triggers.
            if _trigger_active_dip_match:
                _triggers_fired.append("active_dip")
            if _trigger_high_activity_runner_match:
                _triggers_fired.append("high_activity_runner")
            if _trigger_confirmed_dip_match:
                _triggers_fired.append("confirmed_dip")
            if _trigger_low_liq_active_dip_match:
                _triggers_fired.append("low_liq_active_dip")
            # 2026-05-18 — Round-6 anchor mining trigger.
            if _trigger_high_churn_microcap_match:
                _triggers_fired.append("high_churn_microcap")

            # ── Breakthrough-trigger LATE flag (2026-05-16 PM) ─────────────
            # Set after all 6 breakthrough triggers (strong_orderflow,
            # sustained_accumulation, chart_quality_bottom,
            # buyer_momentum_burst, flow_reversal, chart_score_reversal)
            # have been evaluated AND wash-trade guard has applied.
            # This flag is AUTHORITATIVE — gates carve-outs in
            # downstream filters that fire AFTER this point.
            _breakthrough_late_match = bool(
                _trigger_strong_orderflow_match
                or _trigger_sustained_accum_match
                or _trigger_chart_qual_bottom_match
                or _trigger_buyer_momentum_burst_match
                or _trigger_flow_reversal_match
                or _trigger_chart_reversal_match
                # R2 round-2 mining triggers (2026-05-17).
                or _trigger_swing_structure_rsi_match
                or _trigger_channel_pos_swing_match
                # R3 round-3 mining triggers (2026-05-17).
                or _trigger_channel_hvn_match
                or _trigger_shape_wick_match
                or _trigger_cnn_lp_match
                # R4 round-4 mining triggers (2026-05-17).
                or _trigger_clean_consec_ll_match
                # R5 round-5 mining trigger DISABLED — see comment at append site.
                # or _trigger_sweep_holder_liq_match
                # R6 round-6 mining trigger (2026-05-17).
                or _trigger_clean_dip_trend_match
                # young_active_dip (2026-05-17, universe-recorder mining).
                or _trigger_young_active_dip_match
                # Round-5 volume-push triggers (2026-05-18).
                or _trigger_active_dip_match
                or _trigger_high_activity_runner_match
                or _trigger_confirmed_dip_match
                or _trigger_low_liq_active_dip_match
                # Round-6 anchor trigger (2026-05-18).
                or _trigger_high_churn_microcap_match
            )

            # Apply anti-pattern suppression — clears all triggers if
            # the candidate matches a known 0/7 loss cohort.
            if _suppress_reason and _triggers_fired:
                logger.info(
                    f"[DipScanner] anti-pattern SUPPRESS all triggers: "
                    f"{token_symbol} fired={_triggers_fired} reason={_suppress_reason}"
                )
                c["anti_pattern_suppressed"] = c.get("anti_pattern_suppressed", 0) + 1
                _triggers_fired = []
            # clean_break DISABLED 2026-05-15 PM — recent 3d audit (n=11) showed
            # 27% WR / -$17.15 net. Gate G (mtf>=0 + chart>=48) was added first
            # to salvage 4 trades / 50% WR but the user called the remaining
            # signal "sucks" — full disable until a stronger gate is mined.
            # The whole verdict/gate machinery above is left intact (low cost)
            # so the trigger can be re-enabled in one line if conditions change.
            if False and _filter_clean_break_verdict == "PASS" and not _cb_gated:
                _triggers_fired.append("clean_break")
            # ─── RETIRED chart-pattern triggers (2026-05-16) ────────────────
            # Dormancy audit on 81 post-reset trades showed match=0/81 for 15
            # round-5/6/7 chart-pattern triggers (4combo, quiet_pop, etc.).
            # Predicates still compute above and stamp _match to entry_meta
            # for SHADOW audit; the append-to-_triggers_fired is removed so
            # they can't open trades alone. If a token had these as the
            # ONLY signal, it's now blocked. One-line revival per trigger.
            #
            # if _trigger_4combo_match: _triggers_fired.append("4combo")
            # if _trigger_quietpop_match: _triggers_fired.append("quiet_pop")
            # if _trigger_deepbreakout_match: _triggers_fired.append("deep_breakout")
            # if _trigger_capitv_match: _triggers_fired.append("capit_v")
            # if _trigger_engulflow_match: _triggers_fired.append("engulf_low")
            # if _trigger_hc46_match: _triggers_fired.append("hc4_6pct")
            # if _trigger_coillong_match: _triggers_fired.append("coil_long")
            # if _trigger_decay4_match: _triggers_fired.append("range_decay_4bar")
            # if _trigger_decay4of5_match: _triggers_fired.append("range_decay_4of5")
            # if _trigger_coiltv_match: _triggers_fired.append("coil_top_vol")
            # if _trigger_momentum_continuation_match: _triggers_fired.append("momentum_continuation")
            # if _trigger_explosive_break_match: _triggers_fired.append("explosive_break")
            # if _trigger_range_expansion_qualified_match: _triggers_fired.append("range_expansion_qualified")
            # if _trigger_6of7_green_vol_match: _triggers_fired.append("6of7_green_vol")
            # if _trigger_vol_velocity_2grn_match: _triggers_fired.append("vol_velocity_2grn")
            # ─── still active ──────────────────────────────────────────────
            if _trigger_high_regime_match:
                _triggers_fired.append("high_regime")
            if _trigger_hh10_strict_vol_match:
                _triggers_fired.append("hh10_strict_vol")
            if _trigger_hh10_8plus_match:
                _triggers_fired.append("hh10_8plus")
            # ─── DISABLED 2026-05-16: alpha_buyperscold ────────────────────
            # 9 fires, 3W/6L (33% WR), avg -0.65%. Below baseline 48% WR.
            # Loser-cohort mining showed all 6 losses were in clean-linear
            # 30m trends (r²>=0.45); winners in choppier conditions (r²<0.15).
            # Predicate `bs_m5>=3.0 AND pc_h24<50` is too loose — fires on
            # extension-end as often as on bottoms. Disabled rather than
            # gated because the on-chain triggers (strong_orderflow,
            # sustained_accumulation) already cover the real-bottom cases
            # this was meant to catch.
            # if _trigger_alpha_buyperscold_match:
            #     _triggers_fired.append("alpha_buyperscold")
            if _trigger_post_capit_breakout_match:
                _triggers_fired.append("post_capit_breakout")
            if _trigger_beta_retailfresh_match:
                _triggers_fired.append("beta_retailfresh")
            if _trigger_delta_microcap_match:
                _triggers_fired.append("delta_microcap")
            if _trigger_seller_exhaustion_match:
                _triggers_fired.append("seller_exhaustion")
            if _trigger_deep_dip_bottom_match:
                _triggers_fired.append("deep_dip_bottom")
            # 2026-05-17 RETIRED — patient_bottom trigger removed from
            # active firing. PAC 03:22 UTC fired this trigger and bought
            # a dead-volume corpse: dev_pct_remaining=5.1%, 1m_vol_spike=
            # 0.22, buy_size_n_last60s=1, 9 shadow filters all BLOCK.
            # Trigger conditions (vwap_1h_dist<=-3, min_since_peak>=60min)
            # describe a "mature dip" but don't check real-time activity,
            # so it fires on stale-cache corpses indistinguishable from
            # genuine V-bottoms. Match flag still stamped to entry_meta
            # for forensic analysis; no buy contribution.
            # if _trigger_patient_bottom_match:
            #     _triggers_fired.append("patient_bottom")
            if _trigger_informed_cluster_match:
                _triggers_fired.append("informed_cluster")
            if _trigger_grad_window_dip_match:
                _triggers_fired.append("grad_window_dip")
            if _trigger_demand_bottom_match:
                _triggers_fired.append("demand_bottom_compound")
            if _trigger_sweep_rejection_match:
                _triggers_fired.append("sweep_rejection")
            if _trigger_reaccum_demand_match:
                _triggers_fired.append("reaccum_demand")
            if _trigger_extreme_sweep_1m_match:
                _triggers_fired.append("extreme_sweep_1m")
            if _trigger_controlled_greens_5m_match:
                _triggers_fired.append("controlled_greens_5m")
            if _trigger_pullback_in_uptrend_match:
                _triggers_fired.append("pullback_in_uptrend")
            if _trigger_vol_surge_recent_match:
                _triggers_fired.append("vol_surge_recent")
            if _trigger_bullish_engulfing_5m_match:
                _triggers_fired.append("bullish_engulfing_5m")
            if _trigger_mtf_aligned_demand_match:
                _triggers_fired.append("mtf_aligned_demand")
            if _trigger_liq_velocity_match:
                _triggers_fired.append("liq_velocity_big_buyers")
            if _trigger_net_flow_5m_match:
                _triggers_fired.append("net_flow_5m_demand")
            if _trigger_mcap_psych_match:
                _triggers_fired.append("mcap_psych_level")
            if _trigger_whale_conviction_match:
                _triggers_fired.append("whale_conviction")
            if _trigger_fresh_pump_retrace_match:
                _triggers_fired.append("fresh_pump_retrace")
            if _trigger_late_night_fresh_match:
                _triggers_fired.append("late_night_fresh")
            if _trigger_chart_channel_strong_match:
                _triggers_fired.append("chart_channel_strong")
            if _trigger_strong_uptrend_dip_match:
                _triggers_fired.append("strong_uptrend_dip")
            if _trigger_modest_pump_deep_retrace_match:
                _triggers_fired.append("modest_pump_deep_retrace")
            if _trigger_small_pump_shallow_retrace_match:
                _triggers_fired.append("small_pump_shallow_retrace")
            if _trigger_shallow_retrace_fresh_pump_match:
                _triggers_fired.append("shallow_retrace_fresh_pump")
            if _trigger_midcap_quality_accumulation_match:
                _triggers_fired.append("midcap_quality_accumulation")
            if _trigger_fresh_graduate_buyers_match:
                _triggers_fired.append("fresh_graduate_buyers")
            if _trigger_small_pump_fresh_cycles_match:
                _triggers_fired.append("small_pump_fresh_cycles")
            if _trigger_midcap_bigpump_fresh_match:
                _triggers_fired.append("midcap_bigpump_fresh")
            if _trigger_overnight_modest_pump_consol_match:
                _triggers_fired.append("overnight_modest_pump_consol")
            if _trigger_overnight_quiet_accumulation_match:
                _triggers_fired.append("overnight_quiet_accumulation")
            if _trigger_overnight_fresh_small_pump_match:
                _triggers_fired.append("overnight_fresh_small_pump")
            if _trigger_overnight_quality_old_match:
                _triggers_fired.append("overnight_quality_old")
            if _trigger_overnight_micropump_buyers_match:
                _triggers_fired.append("overnight_micropump_buyers")
            if _trigger_overnight_mature_midcap_match:
                _triggers_fired.append("overnight_mature_midcap")
            if _trigger_overnight_3d_bigpump_fresh_age_match:
                _triggers_fired.append("overnight_3d_bigpump_fresh_age")
            if _trigger_overnight_3d_bigpump_midcap_match:
                _triggers_fired.append("overnight_3d_bigpump_midcap")
            if _trigger_overnight_3d_midcap_liq_band_match:
                _triggers_fired.append("overnight_3d_midcap_liq_band")
            if _trigger_overnight_3d_bigpump_avgtrade_match:
                _triggers_fired.append("overnight_3d_bigpump_avgtrade")
            if _trigger_overnight_3d_midcap_mature_cycles_match:
                _triggers_fired.append("overnight_3d_midcap_mature_cycles")
            if _trigger_3d_balanced_h1_fresh_predawn_match:
                _triggers_fired.append("3d_balanced_h1_fresh_predawn")
            if _trigger_3d_small_pump_shallow_fresh_match:
                _triggers_fired.append("3d_small_pump_shallow_fresh")
            if _trigger_3d_active_5m_small_pump_fresh_match:
                _triggers_fired.append("3d_active_5m_small_pump_fresh")
            if _trigger_3d_compound_buyers_fresh_age_match:
                _triggers_fired.append("3d_compound_buyers_fresh_age")
            if _trigger_3d_strong_h1_fresh_daytime_match:
                _triggers_fired.append("3d_strong_h1_fresh_daytime")
            if _trigger_3d_midrange_midcap_predawn_match:
                _triggers_fired.append("3d_midrange_midcap_predawn")
            if _trigger_3d_bigpump_midcap_24_7_match:
                _triggers_fired.append("3d_bigpump_midcap_24_7")
            if _trigger_3d_compound_midcap_fresh_age_match:
                _triggers_fired.append("3d_compound_midcap_fresh_age")
            if _trigger_3d_extreme_h1_midliq_predawn_match:
                _triggers_fired.append("3d_extreme_h1_midliq_predawn")
            if _trigger_3d_compound_strong5m_midtrade_match:
                _triggers_fired.append("3d_compound_strong5m_midtrade")
            if _trigger_3d_mature_midcap_postmidnight_match:
                _triggers_fired.append("3d_mature_midcap_postmidnight")
            if _trigger_3d_liq_midcap_compound_match:
                _triggers_fired.append("3d_liq_midcap_compound")
            if _trigger_3d_h6_fresh_age_compound_match:
                _triggers_fired.append("3d_h6_fresh_age_compound")
            if _trigger_3d_h1_midcap_liq_24_7_match:
                _triggers_fired.append("3d_h1_midcap_liq_24_7")
            if _trigger_3d_h6_smallpump_midtrade_match:
                _triggers_fired.append("3d_h6_smallpump_midtrade")
            if _trigger_3d_h6_strong5m_old_match:
                _triggers_fired.append("3d_h6_strong5m_old")
            if _trigger_3d_h6_midcap_deepdrop_match:
                _triggers_fired.append("3d_h6_midcap_deepdrop")
            if _trigger_3d_bigpump_midcap_compound_match:
                _triggers_fired.append("3d_bigpump_midcap_compound")
            if _trigger_3d_midcap_fresh_age_compound_match:
                _triggers_fired.append("3d_midcap_fresh_age_compound")

            # 1s triggers fire LATER (after 1s feature compute) — allow
            # dippy candidates with NO classic-trigger match to pass this
            # bail-out so the 1s logic can re-check downstream.
            try:
                _pc24_for_1s = float(pc_h24) if pc_h24 is not None else 0.0
            except Exception:
                _pc24_for_1s = 0.0
            _1s_eligible_standalone = (not _triggers_fired) and _pc24_for_1s <= -3.0

            if not _triggers_fired and not _1s_eligible_standalone:
                logger.info(
                    f"[DipScanner] BLOCKED by all triggers: "
                    f"{token_symbol} cb_reasons={','.join(_filter_clean_break_block_reasons)}"
                )
                continue

            _trigger_source = (
                "_".join(_triggers_fired) if len(_triggers_fired) > 1
                else (_triggers_fired[0] if _triggers_fired else "1s_pending")
            )
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
                if _trigger_alpha_buyperscold_match:
                    _alt_reasons.extend(_trigger_alpha_buyperscold_reasons)
                if _trigger_beta_retailfresh_match:
                    _alt_reasons.extend(_trigger_beta_retailfresh_reasons)
                if _trigger_delta_microcap_match:
                    _alt_reasons.extend(_trigger_delta_microcap_reasons)
                if _trigger_seller_exhaustion_match:
                    _alt_reasons.extend(_trigger_seller_exhaustion_reasons)
                if _trigger_deep_dip_bottom_match:
                    _alt_reasons.extend(_trigger_deep_dip_bottom_reasons)
                if _trigger_patient_bottom_match:
                    _alt_reasons.extend(_trigger_patient_bottom_reasons)
                if _trigger_informed_cluster_match:
                    _alt_reasons.extend(_trigger_informed_cluster_reasons)
                if _trigger_grad_window_dip_match:
                    _alt_reasons.extend(_trigger_grad_window_dip_reasons)
                if _trigger_demand_bottom_match:
                    _alt_reasons.extend(_trigger_demand_bottom_reasons)
                if _trigger_sweep_rejection_match:
                    _alt_reasons.extend(_trigger_sweep_rejection_reasons)
                if _trigger_reaccum_demand_match:
                    _alt_reasons.extend(_trigger_reaccum_demand_reasons)
                if _trigger_extreme_sweep_1m_match:
                    _alt_reasons.extend(_trigger_extreme_sweep_1m_reasons)
                if _trigger_controlled_greens_5m_match:
                    _alt_reasons.extend(_trigger_controlled_greens_5m_reasons)
                if _trigger_pullback_in_uptrend_match:
                    _alt_reasons.extend(_trigger_pullback_in_uptrend_reasons)
                if _trigger_vol_surge_recent_match:
                    _alt_reasons.extend(_trigger_vol_surge_recent_reasons)
                if _trigger_bullish_engulfing_5m_match:
                    _alt_reasons.extend(_trigger_bullish_engulfing_5m_reasons)
                if _trigger_mtf_aligned_demand_match:
                    _alt_reasons.extend(_trigger_mtf_aligned_demand_reasons)
                if _trigger_liq_velocity_match:
                    _alt_reasons.extend(_trigger_liq_velocity_reasons)
                if _trigger_net_flow_5m_match:
                    _alt_reasons.extend(_trigger_net_flow_5m_reasons)
                if _trigger_mcap_psych_match:
                    _alt_reasons.extend(_trigger_mcap_psych_reasons)
                if _trigger_whale_conviction_match:
                    _alt_reasons.extend(_trigger_whale_conviction_reasons)
                if _trigger_modest_pump_deep_retrace_match:
                    _alt_reasons.extend(_trigger_modest_pump_deep_retrace_reasons)
                if _trigger_small_pump_shallow_retrace_match:
                    _alt_reasons.extend(_trigger_small_pump_shallow_retrace_reasons)
                if _trigger_shallow_retrace_fresh_pump_match:
                    _alt_reasons.extend(_trigger_shallow_retrace_fresh_pump_reasons)
                if _trigger_midcap_quality_accumulation_match:
                    _alt_reasons.extend(_trigger_midcap_quality_accumulation_reasons)
                if _trigger_fresh_graduate_buyers_match:
                    _alt_reasons.extend(_trigger_fresh_graduate_buyers_reasons)
                if _trigger_small_pump_fresh_cycles_match:
                    _alt_reasons.extend(_trigger_small_pump_fresh_cycles_reasons)
                if _trigger_midcap_bigpump_fresh_match:
                    _alt_reasons.extend(_trigger_midcap_bigpump_fresh_reasons)
                if _trigger_overnight_modest_pump_consol_match:
                    _alt_reasons.extend(_trigger_overnight_modest_pump_consol_reasons)
                if _trigger_overnight_quiet_accumulation_match:
                    _alt_reasons.extend(_trigger_overnight_quiet_accumulation_reasons)
                if _trigger_overnight_fresh_small_pump_match:
                    _alt_reasons.extend(_trigger_overnight_fresh_small_pump_reasons)
                if _trigger_overnight_quality_old_match:
                    _alt_reasons.extend(_trigger_overnight_quality_old_reasons)
                if _trigger_overnight_micropump_buyers_match:
                    _alt_reasons.extend(_trigger_overnight_micropump_buyers_reasons)
                if _trigger_overnight_mature_midcap_match:
                    _alt_reasons.extend(_trigger_overnight_mature_midcap_reasons)
                if _trigger_overnight_3d_bigpump_fresh_age_match:
                    _alt_reasons.extend(_trigger_overnight_3d_bigpump_fresh_age_reasons)
                if _trigger_overnight_3d_bigpump_midcap_match:
                    _alt_reasons.extend(_trigger_overnight_3d_bigpump_midcap_reasons)
                if _trigger_overnight_3d_midcap_liq_band_match:
                    _alt_reasons.extend(_trigger_overnight_3d_midcap_liq_band_reasons)
                if _trigger_overnight_3d_bigpump_avgtrade_match:
                    _alt_reasons.extend(_trigger_overnight_3d_bigpump_avgtrade_reasons)
                if _trigger_overnight_3d_midcap_mature_cycles_match:
                    _alt_reasons.extend(_trigger_overnight_3d_midcap_mature_cycles_reasons)
                if _trigger_3d_balanced_h1_fresh_predawn_match:
                    _alt_reasons.extend(_trigger_3d_balanced_h1_fresh_predawn_reasons)
                if _trigger_3d_small_pump_shallow_fresh_match:
                    _alt_reasons.extend(_trigger_3d_small_pump_shallow_fresh_reasons)
                if _trigger_3d_active_5m_small_pump_fresh_match:
                    _alt_reasons.extend(_trigger_3d_active_5m_small_pump_fresh_reasons)
                if _trigger_3d_compound_buyers_fresh_age_match:
                    _alt_reasons.extend(_trigger_3d_compound_buyers_fresh_age_reasons)
                if _trigger_3d_strong_h1_fresh_daytime_match:
                    _alt_reasons.extend(_trigger_3d_strong_h1_fresh_daytime_reasons)
                if _trigger_3d_midrange_midcap_predawn_match:
                    _alt_reasons.extend(_trigger_3d_midrange_midcap_predawn_reasons)
                if _trigger_3d_bigpump_midcap_24_7_match:
                    _alt_reasons.extend(_trigger_3d_bigpump_midcap_24_7_reasons)
                if _trigger_3d_compound_midcap_fresh_age_match:
                    _alt_reasons.extend(_trigger_3d_compound_midcap_fresh_age_reasons)
                if _trigger_3d_extreme_h1_midliq_predawn_match:
                    _alt_reasons.extend(_trigger_3d_extreme_h1_midliq_predawn_reasons)
                if _trigger_3d_compound_strong5m_midtrade_match:
                    _alt_reasons.extend(_trigger_3d_compound_strong5m_midtrade_reasons)
                if _trigger_3d_mature_midcap_postmidnight_match:
                    _alt_reasons.extend(_trigger_3d_mature_midcap_postmidnight_reasons)
                if _trigger_3d_liq_midcap_compound_match:
                    _alt_reasons.extend(_trigger_3d_liq_midcap_compound_reasons)
                if _trigger_3d_h6_fresh_age_compound_match:
                    _alt_reasons.extend(_trigger_3d_h6_fresh_age_compound_reasons)
                if _trigger_3d_h1_midcap_liq_24_7_match:
                    _alt_reasons.extend(_trigger_3d_h1_midcap_liq_24_7_reasons)
                if _trigger_3d_h6_smallpump_midtrade_match:
                    _alt_reasons.extend(_trigger_3d_h6_smallpump_midtrade_reasons)
                if _trigger_3d_h6_strong5m_old_match:
                    _alt_reasons.extend(_trigger_3d_h6_strong5m_old_reasons)
                if _trigger_3d_h6_midcap_deepdrop_match:
                    _alt_reasons.extend(_trigger_3d_h6_midcap_deepdrop_reasons)
                if _trigger_3d_bigpump_midcap_compound_match:
                    _alt_reasons.extend(_trigger_3d_bigpump_midcap_compound_reasons)
                if _trigger_3d_midcap_fresh_age_compound_match:
                    _alt_reasons.extend(_trigger_3d_midcap_fresh_age_compound_reasons)
                logger.info(
                    f"[DipScanner] ENTRY via {_trigger_source} (clean_break BLOCKed): "
                    f"{token_symbol} {','.join(_alt_reasons)}"
                )
            c[f"trigger_source_{_trigger_source}"] = c.get(
                f"trigger_source_{_trigger_source}", 0
            ) + 1

            # ── filter_solo_decay — ENFORCED 2026-05-11 ─────────────────────
            # Block solo clean_break / high_regime entries on deeply-decayed
            # old tokens. These are the "post-pump dead-cat after major
            # decay" class — what AVA8 (2026-05-11 15:52, lost -$1.70)
            # and ELIEN (2026-05-11 16:16, bleeding -3.88%) both were.
            #
            # Gate: trigger_source ∈ {clean_break, high_regime} alone
            #       AND lifecycle_age_hours > 168 (>= 7 days old)
            #       AND lifecycle_h24_ratio < 0.20 (>=80% off 24h peak)
            #
            # Confluence triggers (clean_break_high_regime, etc.) are NOT
            # blocked — those have 71% WR / +$0.30/trade in post_cb.
            # The solo-trigger class has 46-48% WR / -$0.39 to -$1.01/trade.
            #
            # Validation (lifetime peak/mdd telemetry):
            #   Lifetime: 3 blocks, 0W/3L, -$5.69 (clean losers)
            #   Post_cb:  same 3, all in the same period
            #   Hold-out: 0 blocks (no held-out trades match — won't
            #             affect current-regime forward performance)
            # Layered with triple filter: +$5.91 post_cb, +$3.73 val,
            #   +$2.18 hold-out (positive on every cohort).
            #
            # Catches AVA8 (age=356, h24r=0.113) ✓
            # Catches ELIEN (age=429, h24r=0.154) ✓
            # Preserves Goblin 14:10 (winner, fresh token) ✓
            _fsd_age = _lifecycle_dict.get("lifecycle_age_hours") if _lifecycle_dict else None
            _fsd_h24r = _lifecycle_dict.get("lifecycle_h24_ratio") if _lifecycle_dict else None
            _fsd_solo = _trigger_source in ("clean_break", "high_regime")
            _fsd_block_reasons: list = []
            if (_fsd_solo
                    and isinstance(_fsd_age, (int, float))
                    and isinstance(_fsd_h24r, (int, float))
                    and _fsd_age > 168
                    and _fsd_h24r < 0.20):
                _fsd_block_reasons.append(
                    f"solo_trigger={_trigger_source} AND "
                    f"age={_fsd_age:.0f}h>168 AND "
                    f"h24_ratio={_fsd_h24r:.3f}<0.20 (post-pump corpse)"
                )
            # BREAKTHROUGH carve-out 2026-05-16 PM: rescue when a
            # breakthrough trigger fired. solo_decay targets clean_break/
            # high_regime — but if the candidate ALSO fires a breakthrough
            # trigger, we're not in the solo-trigger class anymore.
            _fsd_breakthrough_carve = bool(
                _fsd_block_reasons and _breakthrough_late_match
            )
            _fsd_verdict = "BLOCK" if (
                _fsd_block_reasons and not _fsd_breakthrough_carve
            ) else "PASS"
            c[f"filter_solo_decay_{_fsd_verdict.lower()}"] = c.get(
                f"filter_solo_decay_{_fsd_verdict.lower()}", 0
            ) + 1
            if _fsd_breakthrough_carve:
                logger.info(
                    f"[DipScanner] filter_solo_decay RESCUED by breakthrough_late: "
                    f"{token_symbol} trigs={_triggers_fired}"
                )
                c["filter_solo_decay_carve_breakthrough"] = c.get(
                    "filter_solo_decay_carve_breakthrough", 0
                ) + 1
            if _fsd_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_solo_decay: "
                    f"{token_symbol} reasons={','.join(_fsd_block_reasons)}"
                )
                continue

            # ── 1s base-formation shadow instrumentation — ADDED 2026-05-11 ────
            # Fetches 30S bars from DexScreener and computes "did a base form
            # before entry?" features. SHADOW only — no filtering. Lets us
            # accumulate forward data to validate the hypothesis from the
            # 2026-05-10 chart analysis: winners enter on tight 1s bases
            # (low volatility last 60s, close near top of recent range);
            # losers enter mid-cascade (high volatility, close near bottom).
            #
            # Cost: 1 extra HTTP per entry decision (~30/day, negligible vs
            # ~50k+ scans). Fail-open: missing features = None.
            _1s_features: dict = {}
            try:
                from feeds.dexscreener_chart_format import parse_chart_bars
                _dex_id = (pair.get("dexId") or "").lower()
                _1s_slug_primary = {
                    "pumpswap": "pumpfundex",
                    "pumpfun": "pumpfundex",
                    "raydium": "solamm",
                    "meteora": "meteora",
                }.get(_dex_id, _dex_id or "pumpfundex")
                # Fallback slug ladder — try alternates on miss. 2026-05-12:
                # 1s feature coverage was ~10% with single-slug single-shot.
                # Most misses are slug mismatches or transient 4xx/5xx;
                # cycling through known Solana DEX slugs recovers many of
                # them. Skip already-tried slug to avoid double-attempt.
                _1s_slug_ladder = [_1s_slug_primary] + [
                    s for s in ("pumpfundex", "solamm", "meteora", "orcawhirl")
                    if s != _1s_slug_primary
                ]
                _1s_pair = pair_addr_for_1m
                _SOL_QUOTE = "So11111111111111111111111111111111111111112"
                # DexScreener uses TLS fingerprinting (Cloudflare) — aiohttp
                # gets 403. Must use curl_cffi with impersonate='chrome'.
                # Wrap sync call in to_thread() to stay async-compatible.
                _1s_raw = None
                _1s_attempts = 0
                try:
                    from curl_cffi import requests as _cf
                    def _fetch_1s_sync(slug: str):
                        _url = (
                            f"https://io.dexscreener.com/dex/chart/amm/v3/{slug}"
                            f"/bars/solana/{_1s_pair}?res=1S&cb=999&q={_SOL_QUOTE}"
                        )
                        try:
                            _r = _cf.get(
                                _url, impersonate="chrome", timeout=8,
                                headers={
                                    "Origin": "https://dexscreener.com",
                                    "Referer": "https://dexscreener.com/",
                                },
                            )
                            if _r.status_code == 200 and _r.content:
                                return _r.content
                        except Exception:
                            return None
                        return None
                    for _slug_try in _1s_slug_ladder[:3]:  # max 3 attempts
                        _1s_attempts += 1
                        _1s_raw = await asyncio.to_thread(_fetch_1s_sync, _slug_try)
                        if _1s_raw:
                            # Parse early — bail if response is non-empty but
                            # has no bars (token not on this DEX slug).
                            try:
                                _peek_bars = parse_chart_bars(_1s_raw)
                                if _peek_bars:
                                    break
                                _1s_raw = None  # empty payload → try next slug
                            except Exception:
                                _1s_raw = None
                        # Short backoff between slug attempts
                        await asyncio.sleep(0.15)
                except Exception as _ee:
                    logger.warning(f"[DipScanner] 1s fetch err: {_ee}")
                if _1s_raw:
                    _1s_bars = parse_chart_bars(_1s_raw)
                    _now_ms = int(time.time() * 1000)
                    _pre60 = [b for b in _1s_bars
                              if _now_ms - 60000 <= b["ts_ms"] < _now_ms]
                    _pre120 = [b for b in _1s_bars
                               if _now_ms - 120000 <= b["ts_ms"] < _now_ms]
                    _1s_features["bars_60s"] = len(_pre60)
                    _1s_features["bars_120s"] = len(_pre120)
                    if _pre60:
                        _h = max(b["high"] for b in _pre60)
                        _l = min(b["low"] for b in _pre60)
                        _mid = (_h + _l) / 2
                        _1s_features["range_pct_60s"] = (
                            (_h - _l) / _mid * 100 if _mid > 0 else 0
                        )
                        _1s_features["red_count_60s"] = sum(
                            1 for b in _pre60 if b["close"] < b["open"]
                        )
                        _1s_features["red_pct_60s"] = (
                            _1s_features["red_count_60s"] / len(_pre60)
                        )
                        _last_close = _pre60[-1]["close"]
                        _1s_features["close_pos_60s"] = (
                            (_last_close - _l) / (_h - _l) if _h > _l else 0.5
                        )
                    if _pre120 and len(_pre120) >= 4:
                        _mid_idx = len(_pre120) // 2
                        _early_v = sum(b["volume_usd"] for b in _pre120[:_mid_idx]) / _mid_idx
                        _late_v = (
                            sum(b["volume_usd"] for b in _pre120[_mid_idx:])
                            / (len(_pre120) - _mid_idx)
                        )
                        if _early_v > 0:
                            _1s_features["vol_decay_120s"] = _late_v / _early_v

                    # ── #4 sweep-reject detection — SHADOW ────────────────────
                    # Pattern: a 30S bar with long lower wick + green close +
                    # high volume = sellers swept lower lows, buyers rejected.
                    # Classic capitulation-reversal "bottom" signal.
                    # Criteria (any of last 3 bars):
                    #   lower_wick > 1.5 * body  AND  close > open  AND
                    #   volume > 1.5 * avg(prior 5 bars)
                    if _pre120 and len(_pre120) >= 6:
                        _swr = False
                        _swr_idx = None
                        for _i in range(max(0, len(_pre120) - 3), len(_pre120)):
                            _b = _pre120[_i]
                            _o, _h, _l, _c, _v = (_b["open"], _b["high"],
                                                  _b["low"], _b["close"],
                                                  _b["volume_usd"])
                            _body = abs(_c - _o)
                            _lower_wick = min(_o, _c) - _l
                            if _body <= 0 or _lower_wick <= 0:
                                continue
                            # Volume context: previous 5 bars before this one
                            _start = max(0, _i - 5)
                            _ctx = _pre120[_start:_i]
                            _avg_v = (sum(b["volume_usd"] for b in _ctx) / len(_ctx)
                                      if _ctx else 0)
                            if (_lower_wick > 1.5 * _body
                                    and _c > _o
                                    and _avg_v > 0 and _v > 1.5 * _avg_v):
                                _swr = True
                                _swr_idx = _i
                                break
                        _1s_features["sweep_reject_detected"] = _swr
                        _1s_features["sweep_reject_bar_idx"] = _swr_idx

                    # ── #4b cascade-reversal detection — SHADOW 2026-05-11 ───
                    # Wider pattern than sweep_reject (single-bar wick+vol).
                    # Cascade-reversal catches Goblin-style bottoms:
                    #   1) 5+ consecutive RED 1s bars (cascade down)
                    #   2) Followed by a GREEN bar closing in top 30% of the
                    #      post-cascade range (reversal confirmation)
                    # Reference: Goblin 2026-05-11 16:43 bottom — 62.5% red
                    # bars with close_pos 0.10, then close_pos jumped to 1.0
                    # at +30s. sweep_reject didn't fire because the pattern
                    # was multi-bar capitulation, not a single long-wick bar.
                    # Window: last 180s (vs sweep_reject's 120s) so we can
                    # see the full cascade + the reversal.
                    _pre180 = [b for b in _1s_bars
                               if _now_ms - 180000 <= b["ts_ms"] < _now_ms]
                    if _pre180 and len(_pre180) >= 8:
                        _max_red_run = 0
                        _max_red_end_idx = -1
                        _cur_run = 0
                        for _i, _b in enumerate(_pre180):
                            if _b["close"] < _b["open"]:
                                _cur_run += 1
                                if _cur_run > _max_red_run:
                                    _max_red_run = _cur_run
                                    _max_red_end_idx = _i
                            else:
                                _cur_run = 0
                        _cascade_rev = False
                        _cascade_rev_cp = None
                        _cascade_rev_pct = None
                        if _max_red_run >= 5 and _max_red_end_idx >= 0:
                            _after = _pre180[_max_red_end_idx + 1:]
                            _green_after = [b for b in _after
                                            if b["close"] > b["open"]]
                            if _green_after and _after:
                                _rev = _green_after[0]
                                _casc_bars = _pre180[
                                    _max_red_end_idx - _max_red_run + 1:
                                    _max_red_end_idx + 1
                                ]
                                _casc_low = min(b["low"] for b in _casc_bars)
                                _range_h = max(b["high"] for b in _after)
                                if _range_h > _casc_low:
                                    _cascade_rev_cp = (
                                        (_rev["close"] - _casc_low)
                                        / (_range_h - _casc_low)
                                    )
                                    if _cascade_rev_cp >= 0.7:
                                        _cascade_rev = True
                                        if _casc_low > 0:
                                            _cascade_rev_pct = (
                                                (_rev["close"] / _casc_low - 1)
                                                * 100
                                            )
                        _1s_features["cascade_length"] = _max_red_run
                        _1s_features["cascade_reversal_detected"] = _cascade_rev
                        _1s_features["cascade_reversal_close_pos"] = _cascade_rev_cp
                        _1s_features["cascade_reversal_pct"] = _cascade_rev_pct

                    # ── #5 structural stop placement — SHADOW ────────────────
                    # The lowest 1s low in last 60s + 0.5% buffer = where a
                    # structural stop would sit. Compare forward to fixed -7%
                    # to see whether structural is tighter (most cases) or
                    # looser (volatile setups).
                    if _pre60 and len(_pre60) >= 2:
                        _recent_low = min(b["low"] for b in _pre60)
                        _last_close = _pre60[-1]["close"]
                        if _last_close > 0:
                            _struct_dist = (
                                (_last_close - _recent_low) / _last_close * 100
                                + 0.5  # 0.5% buffer for slippage
                            )
                            _1s_features["structural_stop_pct"] = _struct_dist

                    # ── #6 V-bottom microstructure features — ENFORCED 2026-05-13
                    # Four new features for richer bottom detection. All derived
                    # from existing _pre60/_pre120 lists — no new API fetches.
                    #
                    # A. green_run_end: consecutive green 1s bars ending at NOW.
                    #    Symmetric to cascade_length. >=2 = momentum reversal.
                    # B. bars_since_low_60s: bars since the 60s lowest low.
                    #    Sweet spot 3-10 (recent enough to be the bottom,
                    #    confirmed enough to have stuck).
                    # C. lower_wick_ratio_last: last 1s bar's lower_wick / body.
                    #    >0.8 = rejection candle; >2.0 = strong absorption.
                    # D. vol_burst_on_reversal_ratio: vol of most recent green
                    #    bar / avg vol of prior 5 bars. >1.5 = real buyer surge.
                    if _pre60 and len(_pre60) >= 6:
                        # A. green_run_end
                        _green_run = 0
                        for _b in reversed(_pre60):
                            if _b["close"] > _b["open"]:
                                _green_run += 1
                            else:
                                break
                        _1s_features["green_run_end"] = _green_run

                        # B. bars_since_low_60s
                        _low_val = min(b["low"] for b in _pre60)
                        _low_idx = max(i for i, b in enumerate(_pre60)
                                       if b["low"] == _low_val)
                        _1s_features["bars_since_low_60s"] = len(_pre60) - 1 - _low_idx

                        # C. lower_wick_ratio_last
                        _lb = _pre60[-1]
                        _body = abs(_lb["close"] - _lb["open"])
                        _lower_wick = min(_lb["open"], _lb["close"]) - _lb["low"]
                        if _body > 0:
                            _1s_features["lower_wick_ratio_last"] = max(0.0, _lower_wick / _body)
                        else:
                            # Doji — use lower_wick / mid_price as a fallback
                            _mid = (_lb["high"] + _lb["low"]) / 2 if _lb["high"] + _lb["low"] > 0 else 1
                            _1s_features["lower_wick_ratio_last"] = max(0.0, _lower_wick / _mid) if _mid > 0 else 0.0

                        # D. vol_burst_on_reversal_ratio: latest green bar's vol /
                        #    avg vol of the 5 bars preceding it.
                        _last_green_idx = None
                        for _i in range(len(_pre60) - 1, -1, -1):
                            if _pre60[_i]["close"] > _pre60[_i]["open"]:
                                _last_green_idx = _i
                                break
                        if _last_green_idx is not None and _last_green_idx >= 1:
                            _ctx_start = max(0, _last_green_idx - 5)
                            _ctx = _pre60[_ctx_start:_last_green_idx]
                            if _ctx:
                                _ctx_avg_v = sum(b["volume_usd"] for b in _ctx) / len(_ctx)
                                if _ctx_avg_v > 0:
                                    _1s_features["vol_burst_on_reversal_ratio"] = (
                                        _pre60[_last_green_idx]["volume_usd"] / _ctx_avg_v
                                    )

                    # ── #7 bottom_score composite — ENFORCED 2026-05-13 ────────
                    # 0-100 weighted score from features 1-6. Used as trigger
                    # gate (>=70 = strong bottom). Weights are equal across
                    # 5 component dimensions; bottom_score is mechanically
                    # rather than statistically optimized (no historical
                    # data yet — Phase 4 of the 4-phase plan).
                    #
                    #   cascade reversal explicit (+25 if cr=True)
                    #   vol exhaustion (+20 if vol_decay>=2)
                    #   price recovery (+20 if close_pos>=0.7, +10 if >=0.5)
                    #   green momentum (+15 if green_run_end>=2)
                    #   bottom freshness (+10 if bars_since_low in [3,10])
                    #   absorption candle (+10 if lower_wick_ratio>=0.8)
                    _score = 0.0
                    if _1s_features.get("cascade_reversal_detected") is True:
                        _score += 25
                    _vd_s = _1s_features.get("vol_decay_120s")
                    if _vd_s is not None and _vd_s >= 2:
                        _score += 20
                    _cp_s = _1s_features.get("close_pos_60s")
                    if _cp_s is not None:
                        if _cp_s >= 0.7:
                            _score += 20
                        elif _cp_s >= 0.5:
                            _score += 10
                    _gr_s = _1s_features.get("green_run_end")
                    if _gr_s is not None and _gr_s >= 2:
                        _score += 15
                    _bsl_s = _1s_features.get("bars_since_low_60s")
                    if _bsl_s is not None and 3 <= _bsl_s <= 10:
                        _score += 10
                    _lwr_s = _1s_features.get("lower_wick_ratio_last")
                    if _lwr_s is not None and _lwr_s >= 0.8:
                        _score += 10
                    _1s_features["bottom_score"] = _score
            except Exception as _e:
                logger.debug(f"[DipScanner] 1s features err: {_e}")

            # ── 1s-based ENFORCED triggers — 2026-05-13 ──────────────────────
            # Three new parallel triggers that fire on 1s microstructure
            # bottom signatures. Run AFTER 1s features are computed so they
            # can both supplement existing triggers AND fire standalone when
            # pc_h24 <= -3 (handled via _1s_eligible_standalone above).
            #
            # Phase 1 — 1s_capit_reversal (Pareto-validated 80% WR, 5/day in
            #   24h in-sample): cascade_reversal=True OR (vd>=2 cp>=0.5 cl>=1)
            # Phase 3 — 1s_v_bottom_strict (mechanism-based, no validation
            #   sample because features are new — relies on textbook V-shape
            #   + absorption + freshness + vol confirmation gates)
            # Phase 4 — 1s_bottom_score_high: bottom_score >= 70 (composite
            #   trigger gate). Uses the weighted score computed above.
            _trigger_1s_capit_reversal_match = False
            _trigger_1s_capit_reversal_reasons: list = []
            _trigger_1s_v_bottom_strict_match = False
            _trigger_1s_v_bottom_strict_reasons: list = []
            _trigger_1s_bottom_score_high_match = False
            _trigger_1s_bottom_score_high_reasons: list = []
            try:
                _1s_cr = _1s_features.get("cascade_reversal_detected") is True
                _1s_vd = _1s_features.get("vol_decay_120s")
                _1s_cp = _1s_features.get("close_pos_60s")
                _1s_cl = _1s_features.get("cascade_length")
                _1s_gr = _1s_features.get("green_run_end")
                _1s_bsl = _1s_features.get("bars_since_low_60s")
                _1s_lwr = _1s_features.get("lower_wick_ratio_last")
                _1s_vbr = _1s_features.get("vol_burst_on_reversal_ratio")
                _1s_score = _1s_features.get("bottom_score") or 0

                # Phase 1 — capit_reversal medium predicate
                if _1s_cr:
                    _trigger_1s_capit_reversal_match = True
                    _trigger_1s_capit_reversal_reasons.append("cascade_reversal_detected=True")
                elif (_1s_vd is not None and _1s_vd >= 2.0
                      and _1s_cp is not None and _1s_cp >= 0.5
                      and _1s_cl is not None and _1s_cl >= 1):
                    _trigger_1s_capit_reversal_match = True
                    _trigger_1s_capit_reversal_reasons.append(
                        f"vd={_1s_vd:.2f}>=2 AND cp={_1s_cp:.2f}>=0.5 AND cl={_1s_cl:.0f}>=1"
                    )

                # Phase 3 — v_bottom_strict: requires green momentum +
                # fresh-but-confirmed bottom + absorption candle + (vol burst
                # OR vol decay). Mechanism-based; no historical sample.
                if (_1s_gr is not None and _1s_gr >= 2
                        and _1s_bsl is not None and 3 <= _1s_bsl <= 10
                        and _1s_lwr is not None and _1s_lwr >= 0.8
                        and (
                            (_1s_vbr is not None and _1s_vbr >= 1.5)
                            or (_1s_vd is not None and _1s_vd >= 2.0)
                        )):
                    _trigger_1s_v_bottom_strict_match = True
                    _trigger_1s_v_bottom_strict_reasons.append(
                        f"green_run={_1s_gr:.0f}>=2 AND bars_since_low={_1s_bsl:.0f} in [3,10] "
                        f"AND lower_wick_ratio={_1s_lwr:.2f}>=0.8 AND "
                        f"(vol_burst={_1s_vbr if _1s_vbr is not None else 0:.2f}>=1.5 OR "
                        f"vol_decay={_1s_vd if _1s_vd is not None else 0:.2f}>=2.0)"
                    )

                # Phase 4 — bottom_score_high
                if _1s_score >= 70:
                    _trigger_1s_bottom_score_high_match = True
                    _trigger_1s_bottom_score_high_reasons.append(
                        f"bottom_score={_1s_score:.0f}>=70"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] 1s trigger eval err: {_e}")

            if _trigger_1s_capit_reversal_match:
                _triggers_fired.append("1s_capit_reversal")
            if _trigger_1s_v_bottom_strict_match:
                _triggers_fired.append("1s_v_bottom_strict")
            if _trigger_1s_bottom_score_high_match:
                _triggers_fired.append("1s_bottom_score_high")

            # ── User watchlist bypass: April-era filter-only mode ───────────
            # When user picked a token deliberately (watchlist), don't gate
            # on a positive trigger pattern. April 28 100% WR architecture
            # was pure negative selection — exclude bad things, buy anything
            # left. For curated tokens we restore that model: if the full
            # filter chain (including Phase 1 promotions filter_topping /
            # filter_chasing_bounce / filter_blowoff_top / filter_vp_poc
            # plus filter_corpse / filter_lp_drain / filter_falling_knife)
            # gives a green light, buy. Trigger pattern is no longer
            # required — the user IS the trigger.
            if _user_watch and not _triggers_fired:
                _triggers_fired.append("user_watchlist_bypass")
                logger.info(
                    f"[DipScanner] USER_WATCHLIST trigger bypass: "
                    f"{token_symbol} — April-era filter-only mode (no real "
                    f"trigger required, filter chain decides)"
                )

            # Second-chance bail: candidates that reached here via the 1s-
            # eligible standalone gate must now have at least one 1s trigger
            # to proceed. If still empty, bail.
            if not _triggers_fired:
                logger.info(
                    f"[DipScanner] BLOCKED by all triggers (incl 1s): "
                    f"{token_symbol} pc_h24={_pc24_for_1s:.1f}% "
                    f"1s_score={_1s_features.get('bottom_score', 0):.0f}"
                )
                continue

            # Rebuild trigger_source if 1s triggers were added
            _trigger_source = "_".join(_triggers_fired) if len(_triggers_fired) > 1 else _triggers_fired[0]

            # filter_low_mcap_no_edge REMOVED 2026-05-18 PM — was bandaid.
            # Phase 1 (kill bypass + promote 4 filters to ENFORCED) is the
            # real April-era fix. The mcap×trending×trigger gate was extra
            # insurance against a problem Phase 1 already solved, and it
            # blocked productive throughput on small-cap memecoins which
            # is exactly what the bot is supposed to trade.

            # ── filter_dying_volume — SHADOW 2026-05-11 ───────────────────────
            # Block when pre-entry 1s microstructure shows volume dying:
            # late-period vol is <30% of early-period vol over the 120s
            # window. Catches "dead-cat bounce" entries — a small green
            # wick on near-zero buying after a sell wave.
            #
            # AVA8 (2026-05-11 15:52) and ELIEN (2026-05-11 16:16) both
            # had vol_decay 0.19/0.12 respectively. AVA8 lost -$1.70.
            # ELIEN currently bleeding -3.88% within 3 min of entry.
            # Goblin 14:10 winner had vol_decay 7.0 (volume accelerating).
            # Cannot backtest on lifetime — 1s data only flows after the
            # 2026-05-11 res=1S fix deploy.
            #
            # Fail-open if 1s data unavailable (bars_60s is None or empty).
            _fdv_decay = _1s_features.get("vol_decay_120s")
            _fdv_bars = _1s_features.get("bars_60s")
            _fdv_block = False
            _fdv_reasons: list = []
            if (isinstance(_fdv_decay, (int, float))
                    and isinstance(_fdv_bars, (int, float))
                    and _fdv_bars > 0):
                if _fdv_decay < 0.30:
                    _fdv_block = True
                    _fdv_reasons.append(
                        f"1s_vol_decay_120s={_fdv_decay:.3f}<0.30"
                    )
            _fdv_verdict = "BLOCK" if _fdv_block else "PASS"
            c[f"filter_dying_volume_{_fdv_verdict.lower()}"] = (
                c.get(f"filter_dying_volume_{_fdv_verdict.lower()}", 0) + 1
            )
            if _fdv_block:
                logger.info(
                    f"[DipScanner] SHADOW filter_dying_volume would-block: "
                    f"{token_symbol} reasons={','.join(_fdv_reasons)}"
                )

            # ── filter_no_signatures — ENFORCED 2026-05-10 ─────────────────────
            # Block when 0 of 6 positive winner signatures are present (with at
            # least 3 features available — fail-open if chart_reader missed
            # populating most). The 6 winner signatures derive from a Cohen's-d
            # scan of all entry features against winners vs losers on the
            # post-clean_break cohort:
            #   S1 chart_score < 47
            #   S2 chart_structure_5m_state == 'downtrend'
            #   S3 pct_above_vwap_h24 < 0
            #   S4 chart_structure_15m_recent_choch_dir == 'bullish_to_bearish'
            #   S5 chart_structure_15m_state == 'downtrend'
            #   S6 regime == 'up'
            # Validation (post-clean_break, since 2026-05-06):
            #   post_cb (n=62 0-sig) baseline -$103.02 -> $0  Δ +$103.02
            #   orth (n=35 0-sig)   baseline  -$29.22 -> $0  Δ  +$29.22
            #   orth held-out (n=6) baseline   -$4.03 -> $0  Δ   +$4.03
            # Reference: SWATCH 2026-05-10 Buy #3 — chart_score=50.7,
            # 5m_state=uptrend, 15m_state=uptrend, vwap=+66.9%, regime=flat,
            # choch_dir=bearish_to_bullish (none of the 6 sigs hit). Stopped -7%.
            # Trajectory profile of the 0-sig cohort: 60% slow_bleed/fast_stop
            # with avg peak 0-2% and avg MDD -13 to -14%; not recoverable
            # post-entry. Block beat every rescue strategy by +$17 to +$103.
            _sigs_hit = 0
            _sigs_available = 0
            _sigs_present: list = []
            _v = _chart_ctx_dict.get("chart_score")
            if isinstance(_v, (int, float)):
                _sigs_available += 1
                if _v < 47:
                    _sigs_hit += 1; _sigs_present.append("chart<47")
            _v = _chart_ctx_dict.get("chart_structure_5m_state")
            if isinstance(_v, str):
                _sigs_available += 1
                if _v == "downtrend":
                    _sigs_hit += 1; _sigs_present.append("5m_dn")
            _v = vwap_features.get("pct_above_vwap_h24")
            if isinstance(_v, (int, float)):
                _sigs_available += 1
                if _v < 0:
                    _sigs_hit += 1; _sigs_present.append("below_vwap")
            _v = _chart_ctx_dict.get("chart_structure_15m_recent_choch_dir")
            if isinstance(_v, str):
                _sigs_available += 1
                if _v == "bullish_to_bearish":
                    _sigs_hit += 1; _sigs_present.append("choch_b2b")
            _v = _chart_ctx_dict.get("chart_structure_15m_state")
            if isinstance(_v, str):
                _sigs_available += 1
                if _v == "downtrend":
                    _sigs_hit += 1; _sigs_present.append("15m_dn")
            _v = sol_features.get("regime")
            if isinstance(_v, str):
                _sigs_available += 1
                if _v == "up":
                    _sigs_hit += 1; _sigs_present.append("regime_up")
            _filter_no_sig_block_reasons: list = []
            # RETUNED 2026-05-13 PM: sigs_available threshold 3 -> 6.
            # Original validation was on clean_break standalone cohort
            # (post_cb n=62 0-sig saved $103). Today's expanded trigger
            # set (sweep_rejection, demand_bottom_compound, 1s triggers,
            # vwap-cluster) hits this filter as a major post-trigger
            # blocker. Loosening to require ALL 6 sigs evaluable before
            # blocking — only the strictest "every winner signal missed"
            # cases now block. Tokens with 5/6 or fewer sigs evaluable
            # (often fresh-launches or those with partial chart data)
            # pass through. Watching forward WR; revert if drops.
            if _sigs_hit == 0 and _sigs_available >= 6:
                _filter_no_sig_block_reasons.append(
                    f"0_of_{_sigs_available}_winner_signatures "
                    f"(need >=1 of chart<47/5m_dn/below_vwap/"
                    f"choch_b2b/15m_dn/regime_up)"
                )
            _filter_no_signatures_verdict = (
                "BLOCK" if _filter_no_sig_block_reasons else "PASS"
            )
            c[f"filter_no_signatures_{_filter_no_signatures_verdict.lower()}"] = c.get(
                f"filter_no_signatures_{_filter_no_signatures_verdict.lower()}", 0
            ) + 1
            if _filter_no_signatures_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_no_signatures: "
                    f"{token_symbol} sigs_hit=0/{_sigs_available} "
                    f"available={_sigs_available}"
                )
                continue

            # ── filter_chasing_bounce — ENFORCED 2026-05-10 ────────────────────
            # Block when pc_m5 > +5% — the 5m candle at entry is sharply
            # GREEN, indicating the bounce-reversal already happened and
            # we're chasing rather than entering on the dip itself.
            #
            # Validated lifetime sub-distribution:
            #   pc_m5 > +6%: n=22, WR 45.5%, -$21.55  (clear loser cohort)
            #   pc_m5 < -5%: n=24, WR 91.7%, +$34.11  (deepest dippers win)
            #
            # Filter Δ vs do-nothing:
            #   lifetime  Δ +$190.66  (cuts 93 trades net positive)
            #   post_cb   Δ +$56.64   (cuts 31 trades, mostly losers)
            #   orth      Δ +$28.66   (cuts 25 trades, 10W/15L)
            #   held-out  Δ +$5.02    (cuts 4 trades, 1W/3L)
            # All cohorts positive — regime-resilient.
            #
            # Reference: GAYTES 2026-05-10 15:03 (5m=+5.9%, stopped) caught.
            # SWATCH Buy #3 (5m=+4.6%) below threshold — caught by
            # filter_no_signatures instead. Recent winners (CONSENSUS-1,
            # HeavyPulp, ADA, AIFRUITS, goblinmaxxing) all entered on
            # negative or near-zero pc_m5 and pass cleanly.
            #
            # Threshold +5 chosen over +4: same held-out coverage but
            # higher precision on orth ($28.66 vs $20.14 saved) — preserves
            # winners in the +4 to +5 band that mostly recover.
            # Fail-open if pc_m5 missing/zero.
            _filter_chasing_bounce_block_reasons: list = []
            if isinstance(pc_m5, (int, float)) and pc_m5 > 5.0:
                _filter_chasing_bounce_block_reasons.append(
                    f"pc_m5={pc_m5:+.1f}%>+5 "
                    f"(5m bounce already in progress — chasing, not dipping)"
                )
            _filter_chasing_bounce_verdict = (
                "BLOCK" if _filter_chasing_bounce_block_reasons else "PASS"
            )
            c[f"filter_chasing_bounce_{_filter_chasing_bounce_verdict.lower()}"] = c.get(
                f"filter_chasing_bounce_{_filter_chasing_bounce_verdict.lower()}", 0
            ) + 1
            # Record decision (BLOCK or PASS) for retrospective audit.
            try:
                from feeds.filter_shadow_recorder import get_recorder as _gfsr
                _gfsr().record(
                    token_address=token_address,
                    token_symbol=token_symbol,
                    pair=pair,
                    filter_name="filter_chasing_bounce",
                    verdict=_filter_chasing_bounce_verdict,
                    block_reasons=",".join(_filter_chasing_bounce_block_reasons),
                )
            except Exception:
                pass
            # 2026-05-18 — RE-ENFORCED. Lifetime audit (n=128 closed):
            # BLOCK n=9, avg -2.59%, 44% WR (save +23pp). Mira buy 1
            # had pc_m5=+7.5%, blocked by this. Was demoted 2026-05-15
            # after one rare winner (Openhuman +4.5%) — but on n=128 of
            # actual losses, this filter clearly catches more losers
            # than winners. The Openhuman regret is small vs Mira-class
            # losses.
            if _filter_chasing_bounce_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_chasing_bounce: "
                    f"{token_symbol} reasons="
                    f"{','.join(_filter_chasing_bounce_block_reasons)}"
                )
                c["filter_chasing_bounce_block"] = c.get("filter_chasing_bounce_block", 0) + 1
                if not _user_watch:
                    continue
                logger.info(f"[DipScanner] WATCHLIST BYPASS filter_chasing_bounce: {token_symbol}")

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
                    f"[DipScanner] SHADOW filter_double_bear would BLOCK: {token_symbol} "
                    f"reasons={','.join(_filter_double_bear_block_reasons)}"
                )
                # REVERTED to SHADOW 2026-05-16 PM. Universe audit
                # showed blocked cohort 78.4% WR / +7.63% EV vs
                # passed 70.9% WR / +7.27% EV (n_block=829). Marginal
                # but still wrong-direction.

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

            # Filter quote-asymmetry — ENFORCED 2026-05-09.
            # Block when Jupiter's quote shows extreme sell-side slippage
            # asymmetry relative to buy-side: thin sell-side liquidity means
            # any exit will be expensive. Lifetime n=15 fires, 1W/14L,
            # WR 6.7%, save:cut 13.30. Held-out (last 20% of 1011 trades):
            # 5 fires, 1W/4L (WR 20%), net +$7.48. Block rate 1.5%.
            #
            # Methodology: Cohen's-d scan across all numeric entry features
            # surfaced quote_asymmetry_pct as the strongest discriminator
            # (d=-1.01) of stops vs winners. quote_asymmetry_pct = sell
            # impact - buy impact (both as %). >3.5 means sell side is
            # >3.5pp worse than buy side at our $20 size.
            #
            # Worst-loser examples: 3jG3vjwbEu 05-08 17:03 (-$3.50),
            # 3XwDQHMKcn 05-04 03:57 (-$2.43). Both showed quote_asymmetry
            # >3.5 at entry, both stopped at -12% within minutes.
            #
            # Fail-open if quote_asymmetry_pct missing (Jupiter quote
            # failures are common; do not block on missing data).
            _qa_val = jup_features.get("quote_asymmetry_pct")
            _filter_quote_asymmetry_block_reasons: list = []
            if _qa_val is not None and _qa_val > 3.5:
                _filter_quote_asymmetry_block_reasons.append(
                    f"quote_asymmetry={_qa_val:.2f}%>3.5 "
                    f"(thin sell-side liquidity — exit will be expensive)"
                )
            # BREAKTHROUGH carve-out 2026-05-16 PM: rescue when a breakthrough
            # trigger fired. Quote asymmetry is an exit-cost concern; if the
            # candidate is in the 72-100% WR cohort, the expected upside
            # dominates the asymmetry tax.
            _qa_breakthrough_carve = bool(
                _filter_quote_asymmetry_block_reasons and _breakthrough_late_match
            )
            _filter_quote_asymmetry_verdict = (
                "BLOCK" if (_filter_quote_asymmetry_block_reasons
                            and not _qa_breakthrough_carve)
                else "PASS"
            )
            c[f"filter_quote_asymmetry_{_filter_quote_asymmetry_verdict.lower()}"] = c.get(
                f"filter_quote_asymmetry_{_filter_quote_asymmetry_verdict.lower()}", 0
            ) + 1
            if _qa_breakthrough_carve:
                logger.info(
                    f"[DipScanner] filter_quote_asymmetry RESCUED by breakthrough_late: "
                    f"{token_symbol} trigs={_triggers_fired}"
                )
                c["filter_quote_asymmetry_carve_breakthrough"] = c.get(
                    "filter_quote_asymmetry_carve_breakthrough", 0
                ) + 1
            if _filter_quote_asymmetry_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_quote_asymmetry: {token_symbol} "
                    f"reasons={','.join(_filter_quote_asymmetry_block_reasons)}"
                )
                continue

            # Filter 15s-dump — ENFORCED 2026-05-09.
            # Block when net trade flow over the last 15 seconds was
            # < -$500: heavy net selling pressure right before our buy =
            # we're catching a falling knife mid-dump. Lifetime n=15
            # fires, 4W/11L, WR 26.7%, save:cut 4.48. Held-out: 12 fires
            # (majority of fires in test cohort!), 4W/8L (WR 33.3%),
            # net +$17.08. Block rate 1.5%.
            #
            # Methodology: lifetime hunt across numeric features for
            # rules with block_rate <= 10%, save:cut >= 3.0, net >= $15.
            # net_flow_15s_usd < -500 surfaced as one of the few rules
            # whose held-out edge held up (and even improved).
            #
            # Worst-loser examples: 2R2F91ewRg 05-08 08:56 (-$5.15),
            # 3jG3vjwbEu 05-08 17:03 (-$3.50). Both had >$500 net selling
            # in the 15s before entry; both stopped at -12% shortly after.
            #
            # Fail-open if net_flow_15s_usd missing (tier3 features can
            # be empty when recent_trades feed is thin).
            _nf15 = _tier3_features.get("net_flow_15s_usd")
            _filter_15s_dump_block_reasons: list = []
            if _nf15 is not None and _nf15 < -500:
                _filter_15s_dump_block_reasons.append(
                    f"net_flow_15s=${_nf15:+.0f}<-500 "
                    f"(heavy net selling in last 15s — knife catch)"
                )
            _filter_15s_dump_verdict = (
                "BLOCK" if _filter_15s_dump_block_reasons else "PASS"
            )
            c[f"filter_15s_dump_{_filter_15s_dump_verdict.lower()}"] = c.get(
                f"filter_15s_dump_{_filter_15s_dump_verdict.lower()}", 0
            ) + 1
            if _filter_15s_dump_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] SHADOW filter_15s_dump would BLOCK: {token_symbol} "
                    f"reasons={','.join(_filter_15s_dump_block_reasons)}"
                )
                # REVERTED to SHADOW 2026-05-16 PM. Universe audit
                # (n=2049, realistic-PnL sim) showed blocked cohort
                # 78.1% WR / +11.92% EV vs passed 72.3% WR / +5.69% EV.
                # The pc_m5<-10 condition correlates with WINNERS
                # (capitulation bottoms), not losers. Filter was
                # mass-deleting dip-recovery setups.

            # Filter 5m-downtrend — ENFORCED 2026-05-09.
            # Block when the 5m chart has been red for 4+ consecutive
            # candles (= 20+ minutes of sustained 5m decline). The bot's
            # 1m green-confirmation logic fires on noise inside an active
            # 5m downtrend; this filter requires the higher-timeframe
            # trend to have at least paused before entry.
            #
            # Lifetime: 73 fires, 37W/36L, sc 1.22, net +$13.45.
            # Held-out (last 20%): 16 fires, 6W/10L, WR 37.5%,
            #   net +$16.39, sc 2.27.
            # Held-out orthogonal: 13 fires, 4W/9L, WR 30.8%,
            #   net +$17.43, sc 2.89.
            #
            # Mechanism: addresses the "we're entering too early"
            # pattern. Across 18 recent stops avg trough was -20% past
            # entry; the bot was firing on 1m greens while 5m structure
            # was still declining. This filter catches that.
            #
            # Threshold sweep: >=3 too loose (orth WR 58%), >=4 sweet
            # spot (sc 2.89), >=5 sharper but smaller cohort, >=6
            # breaks down (n=3, 67% WR).
            #
            # Fail-open if range_features missing.
            _5m_cr = None
            try:
                _5m_cr = range_features.get("5m_consec_red")
            except Exception:
                _5m_cr = None
            _filter_5m_downtrend_block_reasons: list = []
            if _5m_cr is not None and _5m_cr >= 4:
                _filter_5m_downtrend_block_reasons.append(
                    f"5m_consec_red={_5m_cr}>=4 "
                    f"(20+ min of sustained 5m decline — dip not bottomed)"
                )
            _filter_5m_downtrend_verdict = (
                "BLOCK" if _filter_5m_downtrend_block_reasons else "PASS"
            )
            c[f"filter_5m_downtrend_{_filter_5m_downtrend_verdict.lower()}"] = c.get(
                f"filter_5m_downtrend_{_filter_5m_downtrend_verdict.lower()}", 0
            ) + 1
            if _filter_5m_downtrend_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] SHADOW filter_5m_downtrend would BLOCK: {token_symbol} "
                    f"reasons={','.join(_filter_5m_downtrend_block_reasons)}"
                )
                # REVERTED to SHADOW 2026-05-16 PM. Universe audit
                # showed blocked cohort 75.0% WR / +9.54% EV vs
                # passed 73.8% WR / +7.12% EV. Sustained 5m decline is
                # an entry signal (deeper dip = better recovery), not a
                # danger flag.

            # Filter lower-low — ENFORCED 2026-05-09.
            # Block when 5m swing-low pattern shows a deep lower-low:
            # current swing low is 25%+ below the prior swing low.
            # Mechanism: 5m chart making LL means downtrend is still
            # expanding, not reversing.
            #
            # Lifetime: 48 fires, 20W/28L, WR 41.7%, sc 2.59.
            # Held-out: 43 fires, 17W/26L, WR 39.5%, sc 2.88.
            # Held-out orthogonal: 9 orth fires, 3W/6L, +$28.94, sc 4.92.
            #
            # Tightened to -25 from looser thresholds because the >=
            # -10 / -15 versions cut too many winners.
            #
            # Fail-open if hl_delta_pct missing (requires 2+ swing lows).
            _hl_dp = None
            try:
                _hl_dp = _tier2_features.get("hl_delta_pct")
            except Exception:
                _hl_dp = None
            _filter_lower_low_block_reasons: list = []
            if _hl_dp is not None and _hl_dp < -25.0:
                _filter_lower_low_block_reasons.append(
                    f"hl_delta_pct={_hl_dp:+.1f}%<-25 "
                    f"(5m swing low 25%+ below prior — deep LL pattern)"
                )
            # BREAKTHROUGH carve-out 2026-05-16 PM: rescue when a breakthrough
            # trigger fired. The breakthrough cohort frequently has deep
            # lower-low patterns (pc_h6<0 is the recurring winning archetype),
            # so this filter is structurally blocking them. Carve-out lets
            # the 72-100% WR setups through despite deep LL shape.
            _ll_breakthrough_carve = bool(
                _filter_lower_low_block_reasons and _breakthrough_late_match
            )
            _filter_lower_low_verdict = (
                "BLOCK" if (_filter_lower_low_block_reasons
                            and not _ll_breakthrough_carve)
                else "PASS"
            )
            c[f"filter_lower_low_{_filter_lower_low_verdict.lower()}"] = c.get(
                f"filter_lower_low_{_filter_lower_low_verdict.lower()}", 0
            ) + 1
            if _ll_breakthrough_carve:
                logger.info(
                    f"[DipScanner] filter_lower_low RESCUED by breakthrough_late: "
                    f"{token_symbol} trigs={_triggers_fired}"
                )
                c["filter_lower_low_carve_breakthrough"] = c.get(
                    "filter_lower_low_carve_breakthrough", 0
                ) + 1
            if _filter_lower_low_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_lower_low: {token_symbol} "
                    f"reasons={','.join(_filter_lower_low_block_reasons)}"
                )
                continue

            # Filter LP-drain — ENFORCED 2026-05-09.
            # Block when liquidity pool has shrunk 10%+ over the last
            # 15 minutes. LP shrinking fast = LPs pulling, holders
            # exiting; the dip is happening because the pool is dying.
            #
            # Lifetime: 24 fires, +$36.45.
            # Held-out: 17 fires, 5W/12L, +$28.01, sc 2.41.
            # Held-out orthogonal: 14 fires, 5W/9L, WR 36%, +$18.12, sc 2.41.
            #
            # Threshold sweep: <-8 too loose (WR 39%), <-10 sweet,
            # <-15 plateau, <-20 collapses.
            #
            # 2026-05-13 RETUNED -10 -> -5 after GPXY56UAL incident
            # (lp_delta_15m_pct=-8.89%, lost -$2.03 in 33s — filter at -10
            # let it through). 7d held-out at -5: blocks 40, BLOCK WR 25%
            # vs ALLOW WR 38% (Delta +13%), kills only $14 in winners,
            # saves $81, NET +$67/wk. Clean Delta-WR uplift.
            #
            # Fail-open if lp_delta_15m_pct missing.
            _lp_d = None
            try:
                _lp_d = _lp_flow_dict.get("lp_delta_15m_pct")
            except Exception:
                _lp_d = None
            _filter_lp_drain_block_reasons: list = []
            if _lp_d is not None and _lp_d <= -5.0:
                _filter_lp_drain_block_reasons.append(
                    f"lp_delta_15m={_lp_d:+.1f}%<=-5 "
                    f"(LP draining fast — pool dying, exit will be expensive)"
                )
            _filter_lp_drain_verdict = (
                "BLOCK" if _filter_lp_drain_block_reasons else "PASS"
            )
            c[f"filter_lp_drain_{_filter_lp_drain_verdict.lower()}"] = c.get(
                f"filter_lp_drain_{_filter_lp_drain_verdict.lower()}", 0
            ) + 1
            if _filter_lp_drain_verdict == "BLOCK":
                # CARVE-OUT 2026-05-15: rescue if any high-WR on-chain
                # trigger fires. Lifetime evidence is thin for the
                # trigger × lp_drain intersection (no historical trade
                # in that overlap), so monitor closely. Revert if BLOCK
                # cohort of carve-out rescues underperforms.
                _lp_rescue = (
                    _trigger_strong_orderflow_match
                    or _trigger_sustained_accum_match
                    or _trigger_micro_pattern_match
                    or _trigger_vp_aligned_match
                    or _trigger_quiet_buyer_match
                )
                if _lp_rescue:
                    logger.info(
                        f"[DipScanner] filter_lp_drain rescued by "
                        f"high-WR trigger: {token_symbol} "
                        f"reasons={','.join(_filter_lp_drain_block_reasons)} "
                        f"trigs={_triggers_fired}"
                    )
                    c["filter_lp_drain_rescued"] = c.get("filter_lp_drain_rescued", 0) + 1
                else:
                    logger.info(
                        f"[DipScanner] BLOCKED by filter_lp_drain: {token_symbol} "
                        f"reasons={','.join(_filter_lp_drain_block_reasons)}"
                    )
                    continue

            # Filter buyer-FOMO — ENFORCED 2026-05-09.
            # Block when 60s net-flow imbalance is extremely buy-skewed
            # (>0.9 = >90% of dollar flow is buys). Mechanism: extreme
            # buyer concentration in the last 60s = late chase, retail
            # piling in at the local top while smart money has already
            # bought.
            #
            # Lifetime: 11 fires, sc 5.80.
            # Held-out: 12 fires, 3W/9L, WR 25.0%, +$18.95, sc 5.80.
            # Held-out orthogonal: 12 fires, 3W/9L, WR 25.0%, +$18.95.
            #
            # Counter-intuitive but consistent — high BUYER concentration
            # in tight time window often signals exhaustion, not strength.
            # Real bottom shows balanced flow, not unanimous buying.
            #
            # Fail-open if net_flow_60s_imbalance missing.
            _nf60i = None
            try:
                _nf60i = _tier3_features.get("net_flow_60s_imbalance")
            except Exception:
                _nf60i = None
            _filter_buyer_fomo_block_reasons: list = []
            if _nf60i is not None and _nf60i > 0.9:
                _filter_buyer_fomo_block_reasons.append(
                    f"net_flow_60s_imb={_nf60i:.2f}>0.9 "
                    f"(>90% of 60s flow is buys — late FOMO chase)"
                )
            _filter_buyer_fomo_verdict = (
                "BLOCK" if _filter_buyer_fomo_block_reasons else "PASS"
            )
            c[f"filter_buyer_fomo_{_filter_buyer_fomo_verdict.lower()}"] = c.get(
                f"filter_buyer_fomo_{_filter_buyer_fomo_verdict.lower()}", 0
            ) + 1
            if _filter_buyer_fomo_verdict == "BLOCK":
                # CARVE-OUT 2026-05-15: rescue if a triangulating on-chain
                # trigger fires. filter_buyer_fomo flags pure 60s buyer
                # concentration as "late FOMO chase", but the new triggers
                # ALL require multi-axis alignment (flow + mtf + bs_m5 or
                # bs_h1/h6) — that's structural accumulation, not lone
                # FOMO. Without this carve-out, the filter mechanically
                # blocks every fire of strong_orderflow / sustained_accum.
                _fomo_rescue = (
                    _trigger_strong_orderflow_match
                    or _trigger_sustained_accum_match
                    or _trigger_micro_pattern_match
                    or _trigger_vp_aligned_match
                    or _trigger_quiet_buyer_match
                )
                if _fomo_rescue:
                    logger.info(
                        f"[DipScanner] filter_buyer_fomo rescued by "
                        f"triangulating trigger: {token_symbol} "
                        f"trigs={_triggers_fired}"
                    )
                    c["filter_buyer_fomo_rescued"] = c.get(
                        "filter_buyer_fomo_rescued", 0
                    ) + 1
                else:
                    logger.info(
                        f"[DipScanner] SHADOW filter_buyer_fomo would BLOCK: {token_symbol} "
                        f"reasons={','.join(_filter_buyer_fomo_block_reasons)}"
                    )
                    # REVERTED to SHADOW 2026-05-16 PM. Audit on live trade
                    # data showed block-cohort 50% WR / +1.08% avg vs
                    # pass-cohort 38% WR / -2.15% avg (n=8 vs n=82). The
                    # filter direction is wrong — high net_flow_60s_imb
                    # correlates with WINNERS, not losers, in our actual
                    # stamped data.

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

            # ── filter_low_volatility — ENFORCED 2026-05-12 ──────────────────
            # Block "dead token" pattern: when chart_p90_body_pct < 1.0, the
            # token's biggest 1m candles in the last hour barely moved 1% —
            # TP1 (+5%) essentially unreachable within our hold window.
            #
            # Shadow-mode validation (recent 7d, n=366):
            #   BLOCK cohort: n=31, WR 16%, total $-3.86
            #   PASS cohort:  n=292, WR 39%
            #   WR gap: +22.9pp on PASS side — strongest single discriminator
            #   in the shadow filter audit (5/12 mining session).
            #
            # Original simulation evidence (n=854 entries across 21 tokens):
            #   MEGR (p90_body 0.6%, 79 entries, 0% WR all flats), ROAF
            #   (p90 0.1%, 56 entries, all flats), LOL (p90 1.0%, 26
            #   entries, all flats) — collectively 161 entries the bot
            #   COULD NOT WIN on. Winning tokens averaged p90_body 3.7%.
            #
            # Cost: kills 5 winners per ~7d (small saves we forgo).
            # Save: blocks 26 losers per ~7d, $0.55/day net.
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
                    f"[DipScanner] BLOCKED by filter_low_volatility: "
                    f"{token_symbol} reasons={','.join(_filter_low_vol_block_reasons)}"
                )
                if not _user_watch:
                    continue
                logger.info(f"[DipScanner] WATCHLIST BYPASS filter_low_volatility: {token_symbol}")

            # ═══ DEFENSIVE FILTERS (LOSER-PATTERN MINING) — ENFORCED 2026-05-15 ═══
            # Surfaced by mine_losers_deep.py — three high-precision filters that
            # block specific bleeding cohorts while preserving 90%+ of buy volume.
            # Each shows positive NET $ (loss-$-saved minus win-$-foregone) over
            # 16d historical data.

            # Compute current CT time once for filter use.
            try:
                from zoneinfo import ZoneInfo as _ZI
                _flt_now_ct = datetime.now(_ZI("America/Chicago"))
                _flt_h = _flt_now_ct.hour
                _flt_wd = _flt_now_ct.weekday()  # Mon=0..Sun=6
                _flt_is_wknd = _flt_wd in (5, 6)
            except Exception:
                _flt_h = -1
                _flt_wd = -1
                _flt_is_wknd = False

            # ── filter_dead_5m_eve_wknd (F2) — n=70 historical, 17.1% WR ──
            # Blocks: bs_m5 < 0.8 AND hour[17,22) AND weekend.
            # Lifetime: blocks 4.4 trades/day, saves +$307 net (lost $72 in
            # blocked winners but saved $379 in avoided losers).
            _filter_dead_5m_eve_wknd_block_reasons: list = []
            try:
                _f2_bsm5 = float(ratio_m5) if ratio_m5 not in (None, float("inf")) else None
                if (
                    _f2_bsm5 is not None and _f2_bsm5 < 0.8
                    and 17 <= _flt_h < 22
                    and _flt_is_wknd
                ):
                    _filter_dead_5m_eve_wknd_block_reasons.append(
                        f"bs_m5={_f2_bsm5:.2f}<0.8 AND hour={_flt_h}∈[17,22) "
                        f"AND dow={_flt_wd} (weekend)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] filter_dead_5m_eve_wknd err: {_e}")
            _filter_dead_5m_eve_wknd_verdict = "BLOCK" if _filter_dead_5m_eve_wknd_block_reasons else "PASS"
            c[f"filter_dead_5m_eve_wknd_{_filter_dead_5m_eve_wknd_verdict.lower()}"] = c.get(
                f"filter_dead_5m_eve_wknd_{_filter_dead_5m_eve_wknd_verdict.lower()}", 0
            ) + 1
            if _filter_dead_5m_eve_wknd_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_dead_5m_eve_wknd: "
                    f"{token_symbol} reasons={','.join(_filter_dead_5m_eve_wknd_block_reasons)}"
                )
                continue

            # ── filter_saturday_eve_midliq (F4) — n=30 historical, 3.3% WR ──
            # Blocks: liq[$100k,$250k) AND hour[17,22) AND dow==5 (Saturday).
            # Lifetime: blocks 1.9 trades/day, saves +$228 (only 1 winner $16,
            # saved $244 in losers — best $-saved-per-buy-blocked ratio).
            _filter_sat_eve_midliq_block_reasons: list = []
            try:
                _f4_liq = float(liquidity_usd) if "liquidity_usd" in dir() and liquidity_usd is not None else None
                if _f4_liq is None:
                    _f4_liq = float((pair.get("liquidity") or {}).get("usd") or 0) if "pair" in dir() else None
                if (
                    _f4_liq is not None and 100_000 <= _f4_liq < 250_000
                    and 17 <= _flt_h < 22
                    and _flt_wd == 5  # Saturday
                ):
                    _filter_sat_eve_midliq_block_reasons.append(
                        f"liq=${_f4_liq/1000:.0f}k∈[100k,250k) AND hour={_flt_h}∈[17,22) "
                        f"AND dow=5 (Saturday)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] filter_sat_eve_midliq err: {_e}")
            _filter_sat_eve_midliq_verdict = "BLOCK" if _filter_sat_eve_midliq_block_reasons else "PASS"
            c[f"filter_sat_eve_midliq_{_filter_sat_eve_midliq_verdict.lower()}"] = c.get(
                f"filter_sat_eve_midliq_{_filter_sat_eve_midliq_verdict.lower()}", 0
            ) + 1
            if _filter_sat_eve_midliq_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_sat_eve_midliq: "
                    f"{token_symbol} reasons={','.join(_filter_sat_eve_midliq_block_reasons)}"
                )
                continue

            # ── filter_microcap_buyer_trap (F5) — n=67 historical, 22.4% WR ──
            # Loosened 2026-05-16 after re-mining on 4d post-reset data showed
            # original threshold caught only 1/81 trades. New band:
            #   bs_h1[1.0,1.4) AND mcap[$500k,$5M) AND liq[$100k,$300k)
            # captures 7 trades (6 losers + 1 breakeven, 0 winners hurt):
            #   TCLAW 05-13 -8.65%, AMERICA 05-13 -9.23%, TCLAW 05-13 -4.30%,
            #   WORLDCUP 05-13 -4.91%, RAGEGUY 05-14 0.00%, RAGEGUY 05-14
            #   -4.86%, DIRECTOR 05-15 -22.71%.
            # Mechanism: mid-cap meme ($0.7M-$4.4M), thin liq ($100-250k),
            # only marginally bullish h1 buyer flow (bs_h1 1.0-1.4) = post-
            # pump dead-cat trap. Net +$10.93 saved over 4 days. 100%
            # precision, 0% winner-block. Original lifetime mining (n=67,
            # +$273 saved) prompted the tighter band; the regime has since
            # widened the trap pattern, so we widen the gate to match.
            _filter_microcap_trap_block_reasons: list = []
            try:
                _f5_bsh1 = float(bs_h1) if bs_h1 not in (None, float("inf")) else None
                _f5_mc = float(mcap) if mcap is not None else None
                _f5_liq = float(liquidity_usd) if "liquidity_usd" in dir() and liquidity_usd is not None else None
                if _f5_liq is None:
                    _f5_liq = float((pair.get("liquidity") or {}).get("usd") or 0) if "pair" in dir() else None
                if (
                    _f5_bsh1 is not None and 1.0 <= _f5_bsh1 < 1.4
                    and _f5_mc is not None and 500_000 <= _f5_mc < 5_000_000
                    and _f5_liq is not None and 100_000 <= _f5_liq < 300_000
                ):
                    _filter_microcap_trap_block_reasons.append(
                        f"bs_h1={_f5_bsh1:.2f} AND mcap=${_f5_mc/1e6:.2f}M "
                        f"AND liq=${_f5_liq/1000:.0f}k (microcap buyer trap)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] filter_microcap_trap err: {_e}")
            _filter_microcap_trap_verdict = "BLOCK" if _filter_microcap_trap_block_reasons else "PASS"
            c[f"filter_microcap_trap_{_filter_microcap_trap_verdict.lower()}"] = c.get(
                f"filter_microcap_trap_{_filter_microcap_trap_verdict.lower()}", 0
            ) + 1
            if _filter_microcap_trap_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_microcap_trap: "
                    f"{token_symbol} reasons={','.join(_filter_microcap_trap_block_reasons)}"
                )
                continue

            # ── filter_clean_break_p90 — ENFORCED 2026-05-13 ─────────────────
            # Clean_break trigger requires chart_p90_body_pct > 5.0 to fire.
            # Removes clean_break from _triggers_fired when token's 90th-percentile
            # 1m body % is <= 5% — those tokens drift, they don't run, so a
            # "breakout" rarely reaches TP1 (+5%) after entry. If clean_break
            # was the only trigger, the buy is blocked.
            #
            # Distinct from universal filter_low_volatility (<1.0) which catches
            # dead tokens. This catches the 1.0-5.0 "drift" band — tokens that
            # move but not enough for clean_break to work. high_regime has
            # positive expectancy at p90 1-3 so this gate is clean_break-specific.
            #
            # Validation (post-May-7 in-window clean_break, n=101):
            #   - baseline: 39 bottoms, 16 mid, 46 knives, -$22.69 total
            #   - REQ p90>5 BLOCK 66 cb trades: saves $28.13 over ~5d (~$5.6/day)
            #   - KEEP 24 cb trades: 16 bot, 3 mid, 5 knife, +$5.44, 67% WR
            #   - Net: flips clean_break from negative to positive expectancy
            #
            # Fail-open if chart_p90_body_pct missing.
            _filter_cb_p90_block_reasons: list = []
            if (
                "clean_break" in _triggers_fired
                and _chart_p90_body_pct is not None
                and _chart_p90_body_pct <= 5.0
            ):
                _filter_cb_p90_block_reasons.append(
                    f"p90_body={_chart_p90_body_pct:.2f}%<=5.0 "
                    f"(drift token — clean_break needs >5% candles for TP1 reach)"
                )
            _filter_cb_p90_verdict = (
                "BLOCK" if _filter_cb_p90_block_reasons else "PASS"
            )
            c[f"filter_clean_break_p90_{_filter_cb_p90_verdict.lower()}"] = c.get(
                f"filter_clean_break_p90_{_filter_cb_p90_verdict.lower()}", 0
            ) + 1
            if _filter_cb_p90_verdict == "BLOCK":
                _triggers_fired = [t for t in _triggers_fired if t != "clean_break"]
                if not _triggers_fired:
                    logger.info(
                        f"[DipScanner] BLOCKED by filter_clean_break_p90 "
                        f"(no other triggers): {token_symbol} "
                        f"reasons={','.join(_filter_cb_p90_block_reasons)}"
                    )
                    continue
                logger.info(
                    f"[DipScanner] clean_break removed by filter_clean_break_p90: "
                    f"{token_symbol} reasons={','.join(_filter_cb_p90_block_reasons)} "
                    f"remaining_triggers={_triggers_fired}"
                )
                _trigger_source = (
                    "_".join(_triggers_fired) if len(_triggers_fired) > 1
                    else _triggers_fired[0]
                )

            # ── filter_high_regime_buyvol — ENFORCED 2026-05-13 ──────────────
            # high_regime trigger requires chart_buyvol_ratio_60m > 1.0 to fire.
            # Removes high_regime from _triggers_fired when 60m buy volume is
            # not greater than 60m sell volume — high_regime fires on tokens
            # still in selling pressure (sellers dominant in last hour), which
            # is a fake "dip" buy, not a real absorption.
            #
            # Validation (post-May-7 in-window high_regime, n=34):
            #   - baseline: 14 bot / 3 mid / 17 knives, -$16.65 total, 41% WR
            #   - REQ buyvol>1.0 BLOCK 25 trades: saves $17.20 over ~5d (~$3.4/day)
            #   - KEEP 9 trades: 6 bot / 2 mid / 1 knife, +$0.55, 67% WR
            #   - Flips high_regime from negative to positive expectancy
            #   - Blocked losers all have buyvol < 1.0 (mean ~0.7 — sellers
            #     dominant): clean separator.
            #
            # Threshold tuned: >0.9 saves $16.65 (breakeven), >1.0 saves $17.20
            # (positive), >1.2 saves $20.16 (75% WR but only 4 trades). Picked
            # 1.0 for balance of selectivity and sample size.
            #
            # Fail-open if chart_buyvol_ratio_60m missing.
            _filter_hr_buyvol_block_reasons: list = []
            if (
                "high_regime" in _triggers_fired
                and _chart_buyvol_ratio_60m is not None
                and _chart_buyvol_ratio_60m <= 1.0
            ):
                _filter_hr_buyvol_block_reasons.append(
                    f"buyvol_ratio_60m={_chart_buyvol_ratio_60m:.2f}<=1.0 "
                    f"(sellers dominant in last 60m — not a real absorption)"
                )
            _filter_hr_buyvol_verdict = (
                "BLOCK" if _filter_hr_buyvol_block_reasons else "PASS"
            )
            c[f"filter_high_regime_buyvol_{_filter_hr_buyvol_verdict.lower()}"] = c.get(
                f"filter_high_regime_buyvol_{_filter_hr_buyvol_verdict.lower()}", 0
            ) + 1
            if _filter_hr_buyvol_verdict == "BLOCK":
                _triggers_fired = [t for t in _triggers_fired if t != "high_regime"]
                if not _triggers_fired:
                    logger.info(
                        f"[DipScanner] BLOCKED by filter_high_regime_buyvol "
                        f"(no other triggers): {token_symbol} "
                        f"reasons={','.join(_filter_hr_buyvol_block_reasons)}"
                    )
                    continue
                logger.info(
                    f"[DipScanner] high_regime removed by filter_high_regime_buyvol: "
                    f"{token_symbol} reasons={','.join(_filter_hr_buyvol_block_reasons)} "
                    f"remaining_triggers={_triggers_fired}"
                )
                _trigger_source = (
                    "_".join(_triggers_fired) if len(_triggers_fired) > 1
                    else _triggers_fired[0]
                )

            # ── filter_solo_dropouts — ENFORCED 2026-05-16 ─────────────────
            # Three triggers fail catastrophically on solo fires under the
            # current filter regime. 7d audit per-trigger no-premium WR:
            #   whale_conviction solo : 0W/5L = 0% WR, -$2.65
            #   clean_break (no-prem) : 0W/8 = 0% WR, -$10.30
            #   grad_window_dip (np)  : 14% WR (1/7), -$6.20
            # All three are kept as COMPOUND signals (in combination with
            # other triggers they're fine — the issue is when each fires
            # alone, the evidence is too weak under the post-2026-05-09
            # filter regime).
            #
            # whale_conviction: more clustered top-10 buyers = worse
            #   outcome (inverted relationship); catches coordinated
            #   dumps / exit-liquidity traps.
            # clean_break: 0% WR solo despite being a "high-regime"-class
            #   signal. The clean-break shape on its own is no longer
            #   sufficient under current filters.
            # grad_window_dip: 1/7 WR solo; the grad-window cohort needs
            #   confirming evidence (informed cluster / patient bottom).
            _SOLO_DROPOUT_TRIGGERS = {
                "whale_conviction", "clean_break", "grad_window_dip",
            }
            if len(_triggers_fired) == 1 and _triggers_fired[0] in _SOLO_DROPOUT_TRIGGERS:
                _solo_trig = _triggers_fired[0]
                logger.info(
                    f"[DipScanner] BLOCKED by filter_solo_dropouts: "
                    f"{token_symbol} — {_solo_trig} alone (poor solo WR on 7d)"
                )
                c[f"filter_solo_dropouts_{_solo_trig}_block"] = c.get(
                    f"filter_solo_dropouts_{_solo_trig}_block", 0
                ) + 1
                if not _user_watch:
                    continue
                logger.info(f"[DipScanner] WATCHLIST BYPASS filter_solo_dropouts: {token_symbol}")
            c["filter_solo_dropouts_pass"] = c.get(
                "filter_solo_dropouts_pass", 0
            ) + 1

            # ── filter_premium_required — ENFORCED 2026-05-16 ──────────────
            # Marginal triggers (patient_bottom, informed_cluster,
            # 1s_capit_reversal) show massive WR lift when the "premium
            # quality" compound is satisfied. 7d audit:
            #
            #   Trigger             | no-premium | with-premium
            #   --------------------+------------+-------------
            #   patient_bottom      | 41% WR     | 100% WR (+$3.8)
            #   informed_cluster    | 27% WR     | 100% WR (+$4.6)
            #   1s_capit_reversal   | 25% WR     | 100% WR (+$2.0)
            #
            # Premium compound = whale-tier buy-side flow + high liquidity
            # velocity. All three components must be met:
            #   avg_trade_size_h1_usd      >= 116
            #   liq_velocity_h1_usd_per_txn >= 135
            #   p90_buy_size_usd           >= 153
            #
            # Rule: if EVERY fired trigger is in the marginal set AND the
            # premium compound is NOT satisfied, BLOCK. Compound entries
            # (any non-marginal trigger also fired) are unaffected.
            #
            # Fail-open if any premium component is missing (don't block
            # for data sparsity — only block when we KNOW it failed).
            _MARGINAL_TRIGGERS = {
                "patient_bottom", "informed_cluster", "1s_capit_reversal",
                # Expanded 2026-05-16 PM after 24h audit. These triggers showed
                # poor solo + bad-compound P&L when premium signature absent:
                #   whale_conviction (16 fires/24h, -$7.89, 44% WR)
                #   informed_cluster (6 fires, 17% WR, -$4.45)
                #   net_flow_5m_demand (9 fires, 44% WR, -$4.43)
                #   grad_window_dip (7d 0% solo WR, -$1.34)
                #   alpha_buyperscold (33% WR, -$2.03)
                # Adding to MARGINAL set so any compound made up ENTIRELY of
                # these underperformers requires the premium quality
                # signature (whale-tier buy flow). Mixed compounds with
                # strong technical triggers (chart_quality_bottom,
                # mtf_aligned_demand, sustained_accumulation, etc.) still
                # pass — the gate only fires when EVERY trigger is marginal.
                "whale_conviction", "grad_window_dip", "alpha_buyperscold",
                "net_flow_5m_demand",
                # fresh_pump_retrace is brand new (no forward live P&L yet).
                # Treat as marginal until 3-5 days of validation. With premium
                # signature met, it passes; otherwise blocked when solo or
                # marginal-only compound.
                "fresh_pump_retrace",
            }
            _all_marginal = bool(_triggers_fired) and all(
                t in _MARGINAL_TRIGGERS for t in _triggers_fired
            )
            if _all_marginal:
                # Pull premium components from their source dicts (these run
                # earlier in the loop; entry_meta_dict is assembled later).
                # Defensive: volume_velocity_features may not exist on first
                # outer-loop iteration (built later in the loop body).
                _avg_trade = float(avg_trade_size_h1) if avg_trade_size_h1 else None
                try:
                    _liq_vel = volume_velocity_features.get("liq_velocity_h1_usd_per_txn")
                except (NameError, AttributeError):
                    _liq_vel = None
                try:
                    _p90_buy = _trade_log_dict.get("p90_buy_size_usd")
                except (NameError, AttributeError):
                    _p90_buy = None
                _premium_ok = (
                    _avg_trade is not None and _avg_trade >= 116
                    and _liq_vel is not None and _liq_vel >= 135
                    and _p90_buy is not None and _p90_buy >= 153
                )
                _premium_known = (
                    _avg_trade is not None and _liq_vel is not None
                    and _p90_buy is not None
                )
                if _premium_known and not _premium_ok:
                    logger.info(
                        f"[DipScanner] BLOCKED by filter_premium_required: "
                        f"{token_symbol} marginal-only triggers={_triggers_fired} "
                        f"avg_trade={_avg_trade:.1f} liq_vel={_liq_vel:.1f} "
                        f"p90={_p90_buy:.1f} (need >=116,>=135,>=153)"
                    )
                    c["filter_premium_required_block"] = c.get(
                        "filter_premium_required_block", 0
                    ) + 1
                    continue
            c["filter_premium_required_pass"] = c.get(
                "filter_premium_required_pass", 0
            ) + 1

            # ── filter_morning_dead_zone — ENFORCED 2026-05-16 PM ──────────
            # Hour-of-day mining (universe-recorder n=2049) showed CT 7-9
            # combined with mature tokens (age>24h) is a persistent dead
            # zone: avg_exit -10% to -39% across cells. Likely mechanism:
            # US morning wakeup distribution by EU/Asian holders unwinding
            # overnight pumps; mature tokens lack fresh-buyer enthusiasm.
            #
            # Universe stats on dead cells:
            #   CT 7 + age>24h: n=100  won_10pct=28%  avg_exit=-11%
            #   CT 8 + age>24h: n=83   won_10pct=28%  avg_exit=-10%
            #   CT 9 + age>24h: n=69   won_10pct=35%
            #   CT 8 + age 12-24h: n=24  won_10pct=17%
            #
            # Predicate: CT hour in {7, 8, 9} AND pair_age_hours > 24
            # Carve-out: premium signature passes (75-100% WR overrides)
            try:
                from datetime import datetime, timezone, timedelta
                _mdz_hr = (datetime.now(timezone.utc) - timedelta(hours=5)).hour
                _mdz_old = pair_age_hours is not None and float(pair_age_hours) > 24
                _mdz_match = (_mdz_hr in {7, 8, 9} and _mdz_old)
                if _mdz_match:
                    # Premium carve-out (re-using same components)
                    try:
                        _mdz_lv = volume_velocity_features.get("liq_velocity_h1_usd_per_txn")
                    except (NameError, AttributeError):
                        _mdz_lv = None
                    try:
                        _mdz_p90 = _trade_log_dict.get("p90_buy_size_usd")
                    except (NameError, AttributeError):
                        _mdz_p90 = None
                    _mdz_ats = float(avg_trade_size_h1) if avg_trade_size_h1 else None
                    _mdz_premium_ok = (
                        _mdz_ats is not None and _mdz_ats >= 116
                        and _mdz_lv is not None and _mdz_lv >= 135
                        and _mdz_p90 is not None and _mdz_p90 >= 153
                    )
                    if not _mdz_premium_ok:
                        logger.info(
                            f"[DipScanner] BLOCKED by filter_morning_dead_zone: "
                            f"{token_symbol} hour_ct={_mdz_hr} age={pair_age_hours:.1f}h>24 "
                            f"(28-35% WR, -10 to -39% avg_exit on universe data)"
                        )
                        c["filter_morning_dead_zone_block"] = c.get(
                            "filter_morning_dead_zone_block", 0
                        ) + 1
                        continue
                    else:
                        logger.info(
                            f"[DipScanner] filter_morning_dead_zone RESCUED by premium: "
                            f"{token_symbol} hour={_mdz_hr} age={pair_age_hours:.1f}h"
                        )
                        c["filter_morning_dead_zone_carve_premium"] = c.get(
                            "filter_morning_dead_zone_carve_premium", 0
                        ) + 1
            except Exception as _e:
                logger.debug(f"[DipScanner] filter_morning_dead_zone err: {_e}")
            c["filter_morning_dead_zone_pass"] = c.get(
                "filter_morning_dead_zone_pass", 0
            ) + 1

            # ── filter_blowoff_top — REVERTED to SHADOW 2026-05-16 PM ──────
            # Universe-data audit (n=2049 events, realistic-PnL sim) showed
            # blocked cohort: 73.0% WR, +8.06% avg EV
            # passed  cohort: 74.2% WR, +7.55% avg EV
            # Filter is roughly neutral to slightly NEGATIVE on universe
            # scale. The "0/4 WR in our trades" finding was post-cascade
            # selection bias. Demoting to SHADOW — keep stamping for forward
            # analysis but no longer block.
            #
            # Original mining notes preserved below.
            # ── filter_blowoff_top — ENFORCED 2026-05-16 PM ────────────────
            # Block tokens with pc_h24 >= 500% — blow-off-top territory where
            # the 24h pump is so extended that mean-reversion dominates any
            # dip-buy attempt.
            #
            # Mining (universe n=2049, "regime=up gap" investigation):
            #   pc_h24 bucket          n     won_10pct  avg_exit
            #   green +20-100%         303     48%        +1.3%   ← gold zone
            #   green +100-500%        887     54%        -2.7%
            #   green +500%+           366     50%        -5.9%   ← blow-off
            #
            # Our trade cohort (n=88 30d, 26 with pc_h24 stamped):
            #   green +20-100%         15     53% WR     +0.04% avg
            #   green +500%+           4      0%  WR     -3.91% avg
            #
            # The +500% bucket has reasonable peak rate (48-50% hit +10%) but
            # NEGATIVE realized exit (-5.9% avg) — peaks don't hold, mean
            # reversion is the dominant dynamic. Both universe and our small
            # cohort agree.
            #
            # Premium-signature carve-out: avg_trade_size_h1>=116 AND
            # liq_velocity_h1>=135 AND p90_buy_size>=153. These compounds
            # have 75-100% WR overrides — let through even at extended h24.
            _filter_blowoff_block_reasons: list = []
            if isinstance(pc_h24, (int, float)) and pc_h24 >= 500.0:
                _filter_blowoff_block_reasons.append(
                    f"pc_h24={pc_h24:.0f}%>=500 (blow-off top — universe "
                    f"avg_exit -5.9%)"
                )
            _filter_blowoff_premium_rescue = False
            if _filter_blowoff_block_reasons:
                try:
                    _bo_lv = volume_velocity_features.get(
                        "liq_velocity_h1_usd_per_txn"
                    )
                except (NameError, AttributeError):
                    _bo_lv = None
                try:
                    _bo_p90 = _trade_log_dict.get("p90_buy_size_usd")
                except (NameError, AttributeError):
                    _bo_p90 = None
                _bo_ats = float(avg_trade_size_h1) if avg_trade_size_h1 else None
                _filter_blowoff_premium_rescue = (
                    _bo_ats is not None and _bo_ats >= 116
                    and _bo_lv is not None and _bo_lv >= 135
                    and _bo_p90 is not None and _bo_p90 >= 153
                )
                if _filter_blowoff_premium_rescue:
                    logger.info(
                        f"[DipScanner] filter_blowoff_top RESCUED by premium: "
                        f"{token_symbol} pc_h24={pc_h24:.0f}%"
                    )
                    c["filter_blowoff_top_carve_premium"] = c.get(
                        "filter_blowoff_top_carve_premium", 0
                    ) + 1
                    _filter_blowoff_block_reasons = []
            _filter_blowoff_top_verdict = (
                "BLOCK" if _filter_blowoff_block_reasons else "PASS"
            )
            c[f"filter_blowoff_top_{_filter_blowoff_top_verdict.lower()}"] = c.get(
                f"filter_blowoff_top_{_filter_blowoff_top_verdict.lower()}", 0
            ) + 1
            if _filter_blowoff_top_verdict == "BLOCK":
                # 2026-05-18 — RE-ENFORCED. Lifetime audit (n=128 closed
                # trades, BLOCK n=8, avg -3.03%, save +24pp) + Mira-specific:
                # blocks pc_h24>=500% AND vol_spike<0.40 entries. Mira buy 1
                # had pc_h24=+545%, blocked by this. Was reverted to SHADOW
                # 2026-05-16 PM during the "WR climb" reverts, but actual-
                # trade data shows it does block losers more than winners.
                logger.info(
                    f"[DipScanner] BLOCKED by filter_blowoff_top: "
                    f"{token_symbol} reasons={','.join(_filter_blowoff_block_reasons)}"
                )
                c["filter_blowoff_top_block"] = c.get("filter_blowoff_top_block", 0) + 1
                if not _user_watch:
                    continue
                logger.info(f"[DipScanner] WATCHLIST BYPASS filter_blowoff_top: {token_symbol}")

            # ── filter_high_activity_fomo — REVERTED to SHADOW 2026-05-16 PM
            # Universe-data audit (n=2049 events, realistic-PnL sim) showed
            # blocked cohort: 80.6% WR, +11.67% avg EV  ← THE BEST cohort
            # passed  cohort: 67.8% WR, +3.94%  avg EV
            # This filter was BLOCKING THE BEST TRADES. Demoting to SHADOW.
            # The "15% WR on n=20" finding was a post-cascade artifact —
            # only post-filter losers reached our trade sample because
            # winners with high buys_per_min were getting blocked by
            # OTHER upstream filters that have since been adjusted.
            #
            # Original mining notes preserved below.
            # ── filter_high_activity_fomo — ENFORCED 2026-05-16 PM ─────────
            # Block tokens with buys_per_min_recent >= 10 — late-stage FOMO
            # peak signature.
            #
            # Mining (our 30d trade cohort, n=89):
            #   buys_per_min cohort       n     WR     avg_pnl
            #   buys_per_min == 0         17    47%    -1.16%   <-- best
            #   buys_per_min > 0          72    31%    -2.52%
            #   buys_per_min >= 5         39    33%    -2.79%
            #   buys_per_min >= 10        20    15%    -4.63%   <-- worst
            #
            # Counter-intuitive but consistent with universe-mining finding
            # (low buys_h1 = better outcome). High recent buy rate = retail
            # FOMO peak = mean-reversion catches us.
            #
            # Est savings: 20 trades blocked × ~$0.93 avg loss = +$18.60/30d
            # at current sizing. Doesn't affect 0-WR sub-cohort (preserved).
            _filter_high_fomo_block_reasons: list = []
            try:
                _bpm = (_velocity_dict or {}).get("buys_per_min_recent")
                if isinstance(_bpm, (int, float)) and _bpm >= 10:
                    _filter_high_fomo_block_reasons.append(
                        f"buys_per_min_recent={_bpm}>=10 "
                        f"(FOMO peak — n=20 lifetime, 15% WR, -$4.63 avg)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] high_fomo filter err: {_e}")
            _filter_high_fomo_verdict = (
                "BLOCK" if _filter_high_fomo_block_reasons else "PASS"
            )
            c[f"filter_high_activity_fomo_{_filter_high_fomo_verdict.lower()}"] = c.get(
                f"filter_high_activity_fomo_{_filter_high_fomo_verdict.lower()}", 0
            ) + 1
            if _filter_high_fomo_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] SHADOW filter_high_activity_fomo would BLOCK: "
                    f"{token_symbol} reasons={','.join(_filter_high_fomo_block_reasons)}"
                )
                # SHADOW mode — do not block (reverted 2026-05-16 PM)

            # ── filter_post_pump_corpse — ENFORCED 2026-05-16 PM ───────────
            # Block tokens that just had an extreme pump and are now in
            # dying-volume phase. Entry-meta snapshots are STALE — they
            # capture pump-era vol_h1, but by entry execution the rolling
            # hour has dropped most of that volume and the token is dead.
            # Relative metrics (vol_5m_burst_vs_h1) compare to inflated
            # baselines and falsely show "accelerating" on dead tokens.
            #
            # Predicate (either):
            #   (a) pc_h1 >= +500% — extreme single-hour pump, always
            #       followed by mean reversion. PAC/SPCX reference.
            #   (b) pc_h24 >= +200% AND buys_per_min_recent <= 2 —
            #       recently pumped + currently calm = post-pump corpse.
            #
            # Reference: SPCX 2026-05-16 23:17 — pc_h1=+3397%, pc_h24=+421%,
            # buys_per_min=1. Bought into a token whose last hour was a
            # 33x pump, then died. User had to flag manually.
            #
            # Note: this is DIFFERENT from filter_blowoff_top (pc_h24>=500)
            # and filter_high_activity_fomo (buys_per_min>=10). New cohort.
            _filter_corpse_pump_block_reasons: list = []
            try:
                _ppc_pc_h1 = pc_h1
                _ppc_pc_h24 = pc_h24
                _ppc_bpm = (_velocity_dict or {}).get("buys_per_min_recent")
                # (a) extreme h1 pump
                if isinstance(_ppc_pc_h1, (int, float)) and _ppc_pc_h1 >= 500.0:
                    _filter_corpse_pump_block_reasons.append(
                        f"pc_h1={_ppc_pc_h1:.0f}%>=500 (extreme single-hour pump)"
                    )
                # (b) pumped h24 + currently calm
                if (isinstance(_ppc_pc_h24, (int, float)) and _ppc_pc_h24 >= 200.0
                        and isinstance(_ppc_bpm, (int, float)) and _ppc_bpm <= 2):
                    _filter_corpse_pump_block_reasons.append(
                        f"pc_h24={_ppc_pc_h24:.0f}%>=200 AND buys_per_min_recent={_ppc_bpm}<=2 "
                        f"(post-pump corpse: pumped + currently calm)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] post_pump_corpse filter err: {_e}")
            _filter_post_pump_corpse_verdict = (
                "BLOCK" if _filter_corpse_pump_block_reasons else "PASS"
            )
            c[f"filter_post_pump_corpse_{_filter_post_pump_corpse_verdict.lower()}"] = c.get(
                f"filter_post_pump_corpse_{_filter_post_pump_corpse_verdict.lower()}", 0
            ) + 1
            if _filter_post_pump_corpse_verdict == "BLOCK":
                logger.info(
                    f"[DipScanner] BLOCKED by filter_post_pump_corpse: "
                    f"{token_symbol} reasons={','.join(_filter_corpse_pump_block_reasons)}"
                )
                continue

            # ── filter_macro_panic — SHADOW 2026-05-16 PM ──────────────────
            # Macro context gate. Existing regime tag already classifies
            # sol/btc/meme-sector state into up/flat/down (lines 1264-1284).
            # This filter sharpens "down" into "panic" using stricter
            # thresholds — true sector dumps where dip-buy P&L tends to
            # invert regardless of token-level signal quality.
            #
            # Predicate (any one fires → PANIC):
            #   meme_sector_pct_h24 < -10        # sector dump
            #   sol_pc_h1 < -3                   # SOL flash crash
            #   (sol_pc_h4 < -5 AND btc_pc_h4 < -2)  # macro flush
            #
            # Carve-out: premium signature (avg_trade_size_h1 >= 116 AND
            # liq_velocity_h1 >= 135 AND p90_buy_size >= 153) passes
            # through — these are the 75-100% WR compounds that don't
            # care about macro noise.
            #
            # Status: SHADOW. Verdict stamped to entry_meta for forward
            # validation. Promote after 5-7d of paired data confirming
            # avg_exit on PANIC-blocked cohort < -10% vs PASS cohort.
            _filter_macro_panic_block_reasons: list = []
            try:
                _mp_msc = sol_features.get("meme_sector_pct_h24")
                _mp_sh1 = sol_features.get("sol_pc_h1")
                _mp_sh4 = sol_features.get("sol_pc_h4")
                _mp_bh4 = sol_features.get("btc_pc_h4")
                if _mp_msc is not None and _mp_msc < -10.0:
                    _filter_macro_panic_block_reasons.append(
                        f"meme_sector_h24={_mp_msc:.1f}%<-10 (sector dump)"
                    )
                if _mp_sh1 is not None and _mp_sh1 < -3.0:
                    _filter_macro_panic_block_reasons.append(
                        f"sol_pc_h1={_mp_sh1:.1f}%<-3 (SOL flash crash)"
                    )
                if (_mp_sh4 is not None and _mp_sh4 < -5.0
                        and _mp_bh4 is not None and _mp_bh4 < -2.0):
                    _filter_macro_panic_block_reasons.append(
                        f"sol_h4={_mp_sh4:.1f}%<-5 AND btc_h4={_mp_bh4:.1f}%<-2 (macro flush)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] filter_macro_panic err: {_e}")
            _filter_macro_panic_verdict = (
                "BLOCK" if _filter_macro_panic_block_reasons else "PASS"
            )
            # Premium-signature carve-out (would-rescue annotation only —
            # SHADOW filter doesn't actually block, so this just marks
            # whether a future ENFORCED version would have rescued).
            _filter_macro_panic_premium_rescue = False
            if _filter_macro_panic_verdict == "BLOCK":
                try:
                    _mp_lv = volume_velocity_features.get(
                        "liq_velocity_h1_usd_per_txn"
                    )
                except (NameError, AttributeError):
                    _mp_lv = None
                try:
                    _mp_p90 = _trade_log_dict.get("p90_buy_size_usd")
                except (NameError, AttributeError):
                    _mp_p90 = None
                _mp_ats = float(avg_trade_size_h1) if avg_trade_size_h1 else None
                _filter_macro_panic_premium_rescue = (
                    _mp_ats is not None and _mp_ats >= 116
                    and _mp_lv is not None and _mp_lv >= 135
                    and _mp_p90 is not None and _mp_p90 >= 153
                )
            c[f"filter_macro_panic_{_filter_macro_panic_verdict.lower()}"] = c.get(
                f"filter_macro_panic_{_filter_macro_panic_verdict.lower()}", 0
            ) + 1
            if _filter_macro_panic_verdict == "BLOCK":
                _rescue_tag = (
                    " (PREMIUM_RESCUE)" if _filter_macro_panic_premium_rescue
                    else ""
                )
                logger.info(
                    f"[DipScanner] filter_macro_panic SHADOW would-block: "
                    f"{token_symbol} reasons={','.join(_filter_macro_panic_block_reasons)}"
                    f"{_rescue_tag}"
                )

            # ── filter_1h_v_bottom_fake_recovery — ENFORCED 2026-05-13 PM ───
            # Round-5 negative-filter mining: last 1h is GREEN and prior 1h
            # was RED, with current close >= prior open (i.e., a "V-bottom
            # recovery" that erased the prior red candle).
            # Validation on n=55 paired: 0W / 4L blocked. Save: +$9.48 (no
            # winners harmed). Mechanism: tokens where the last 1h is a
            # "v-bottom" reversal mean the bot is entering AFTER the
            # recovery candle — chasing the bounce, which fades.
            #
            # Fail-open if 1h data <2 bars (token too new for 1h history).
            _filter_v_bottom_block_reasons: list = []
            try:
                _vb_h1 = (_chart_data.candles_1h
                          if _chart_data and _chart_data.candles_1h else [])
                if len(_vb_h1) >= 2:
                    _vb_c1 = _vb_h1[-2]; _vb_c2 = _vb_h1[-1]
                    if (_vb_c1.close < _vb_c1.open  # prior red
                            and _vb_c2.close > _vb_c2.open  # current green
                            and _vb_c2.close >= _vb_c1.open):  # erased prior red
                        _filter_v_bottom_block_reasons.append(
                            f"1h_v_bottom_recovery: prior_1h_red "
                            f"({_vb_c1.open:.6f}->{_vb_c1.close:.6f}) "
                            f"-> current_1h_green erased it "
                            f"({_vb_c2.open:.6f}->{_vb_c2.close:.6f})"
                        )
            except Exception as _e:
                logger.debug(f"[DipScanner] 1h_v_bottom filter err: {_e}")
            _filter_v_bottom_verdict = (
                "BLOCK" if _filter_v_bottom_block_reasons else "PASS"
            )
            c[f"filter_1h_v_bottom_{_filter_v_bottom_verdict.lower()}"] = c.get(
                f"filter_1h_v_bottom_{_filter_v_bottom_verdict.lower()}", 0
            ) + 1
            if _filter_v_bottom_verdict == "BLOCK":
                # CARVE-OUT 2026-05-15: rescue if a high-WR trigger fires.
                # The v-bottom-fake-recovery filter flags 1h reversal
                # patterns where the recovery candle erased a prior red.
                # The new triggers triangulate (flow + chart + bs) which
                # is stronger than 1h candle pattern alone.
                _v_rescue = (
                    _trigger_strong_orderflow_match
                    or _trigger_sustained_accum_match
                    or _trigger_flow_reversal_match
                    or _trigger_chart_reversal_match
                    or _trigger_micro_pattern_match
                    or _trigger_vp_aligned_match
                    or _trigger_quiet_buyer_match
                )
                if _v_rescue:
                    logger.info(
                        f"[DipScanner] filter_1h_v_bottom_fake_recovery rescued "
                        f"by high-WR trigger: {token_symbol} trigs={_triggers_fired}"
                    )
                    c["filter_1h_v_bottom_rescued"] = c.get(
                        "filter_1h_v_bottom_rescued", 0
                    ) + 1
                else:
                    logger.info(
                        f"[DipScanner] BLOCKED by filter_1h_v_bottom_fake_recovery: "
                        f"{token_symbol} reasons={','.join(_filter_v_bottom_block_reasons)}"
                    )
                    continue

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
                # 2026-05-18 — RE-ENFORCED. Lifetime audit (n=128 closed):
                # BLOCK n=29, avg -0.99%, save +29pp. Mira buy 1 had
                # macro30=+24.6%, blocked by this. Catches "price up >5%
                # in last 30m" — the knife-catch zone after pumps.
                logger.info(
                    f"[DipScanner] BLOCKED by filter_topping: "
                    f"{token_symbol} reasons={','.join(_filter_topping_block_reasons)}"
                )
                c["filter_topping_block"] = c.get("filter_topping_block", 0) + 1
                if not _user_watch:
                    continue
                logger.info(f"[DipScanner] WATCHLIST BYPASS filter_topping: {token_symbol}")

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
            # PROMOTED 2026-05-14 from SHADOW to ENFORCED.
            # CARVE-OUT 2026-05-14 PM: rescue big-buyer entries
            # (liq_velocity_h1 >= 115). Same logic as filter_turn rescue.
            _big_buyer_carve_out_si = False
            _lv_h1_si = None
            try:
                _txn_b_si = int((txns_h1 or {}).get("buys") or 0)
                _txn_s_si = int((txns_h1 or {}).get("sells") or 0)
                _txn_t_si = _txn_b_si + _txn_s_si
                if _txn_t_si > 0 and vol_h1:
                    _lv_h1_si = float(vol_h1) / _txn_t_si
                    if _lv_h1_si >= 115:
                        _big_buyer_carve_out_si = True
            except Exception:
                pass
            if _filter_seller_imbalance_verdict == "BLOCK" and not _big_buyer_carve_out_si:
                logger.info(
                    f"[DipScanner] BLOCKED by filter_seller_imbalance: "
                    f"{token_symbol} reasons={','.join(_filter_seller_imbalance_block_reasons)}"
                )
                continue
            elif _filter_seller_imbalance_verdict == "BLOCK" and _big_buyer_carve_out_si:
                logger.info(
                    f"[DipScanner] filter_seller_imbalance rescued by big_buyer: "
                    f"{token_symbol} liq_velocity_h1=${_lv_h1_si:.0f}/txn>=115"
                )

            # ── filter_negative_net_flow_5m — ENFORCED 2026-05-14 AM ─────────
            # Round-7-extended overnight finding: both overnight losers
            # (lol420 -$2.45, ANDV -$0.84) had net_flow_5m_usd < 0 (sellers
            # winning at 5m). Both monitored winners (Crack +$0.42, MASCOTS
            # +$0.88) had positive or near-zero net_flow_5m_usd at entry.
            #
            # Lifetime validation on n=34 paired: blocks 21 trades total
            # -$19.66 (8W -$4.31, 13L -$23.97); NET SAVE +$19.66. Passing
            # cohort 13 trades total -$1.68 (effectively breakeven).
            #
            # Held-out validation (excluding 2 tuning losers): 11L blocked
            # ($-20.69), 8W blocked ($-4.31), net save +$16.37 — not overfit.
            #
            # Trade-off: cuts trade volume ~62% but passing cohort flips
            # from net-negative to breakeven. Forgoes some small winners
            # to eliminate the disproportionately-large losers (CHINA -$6,
            # NOGUY -$2.59, etc.).
            #
            # Fail-open if net_flow_5m_usd missing.
            _filter_neg_nf5m_block_reasons: list = []
            try:
                _nf5m_chk = _tier3_features.get("net_flow_5m_usd") if isinstance(_tier3_features, dict) else None
                if _nf5m_chk is not None and float(_nf5m_chk) < 0:
                    _filter_neg_nf5m_block_reasons.append(
                        f"net_flow_5m_usd=${float(_nf5m_chk):+.0f}<0 "
                        f"(sellers winning 5m USD flow)"
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] negative_net_flow_5m err: {_e}")
            _filter_neg_nf5m_verdict = (
                "BLOCK" if _filter_neg_nf5m_block_reasons else "PASS"
            )
            c[f"filter_negative_net_flow_5m_{_filter_neg_nf5m_verdict.lower()}"] = c.get(
                f"filter_negative_net_flow_5m_{_filter_neg_nf5m_verdict.lower()}", 0
            ) + 1
            # CARVE-OUT: same big-buyer rescue (liq_velocity_h1 >= 115).
            _big_buyer_carve_out_nf = False
            _lv_h1_nf = None
            try:
                _txn_b_nf = int((txns_h1 or {}).get("buys") or 0)
                _txn_s_nf = int((txns_h1 or {}).get("sells") or 0)
                _txn_t_nf = _txn_b_nf + _txn_s_nf
                if _txn_t_nf > 0 and vol_h1:
                    _lv_h1_nf = float(vol_h1) / _txn_t_nf
                    if _lv_h1_nf >= 115:
                        _big_buyer_carve_out_nf = True
            except Exception:
                pass
            # CARVE-OUT 2026-05-15: rescue if a strict-flow trigger fires.
            # These triggers all require net_flow_60s_usd > 0 which is the
            # more-current signal than the 5m used by this filter. A 60s
            # positive flow alongside 5m negative flow = recent reversal
            # that this filter is over-blocking.
            _nf5m_trig_rescue = (
                _trigger_strong_orderflow_match
                or _trigger_sustained_accum_match
                or _trigger_flow_reversal_match
                or _trigger_micro_pattern_match
                or _trigger_vp_aligned_match
                or _trigger_quiet_buyer_match
            )
            if _filter_neg_nf5m_verdict == "BLOCK" and not _big_buyer_carve_out_nf and not _nf5m_trig_rescue:
                logger.info(
                    f"[DipScanner] BLOCKED by filter_negative_net_flow_5m: "
                    f"{token_symbol} reasons={','.join(_filter_neg_nf5m_block_reasons)}"
                )
                continue
            elif _filter_neg_nf5m_verdict == "BLOCK" and _big_buyer_carve_out_nf:
                logger.info(
                    f"[DipScanner] filter_negative_net_flow_5m rescued by big_buyer: "
                    f"{token_symbol} liq_velocity_h1=${_lv_h1_nf:.0f}/txn>=115"
                )
            elif _filter_neg_nf5m_verdict == "BLOCK" and _nf5m_trig_rescue:
                logger.info(
                    f"[DipScanner] filter_negative_net_flow_5m rescued by "
                    f"strict-flow trigger: {token_symbol} trigs={_triggers_fired}"
                )
                c["filter_negative_net_flow_5m_rescued"] = c.get(
                    "filter_negative_net_flow_5m_rescued", 0
                ) + 1

            # Helper: compute big-buyer carve-out for the new filter block
            # below. Same logic as filter_seller_imbalance / filter_neg_nf5m.
            def _big_buyer_rescued() -> tuple:
                """Return (rescued: bool, lvh1: float|None)."""
                try:
                    _txn_b = int((txns_h1 or {}).get("buys") or 0)
                    _txn_s = int((txns_h1 or {}).get("sells") or 0)
                    _txn_t = _txn_b + _txn_s
                    if _txn_t > 0 and vol_h1:
                        _lv = float(vol_h1) / _txn_t
                        return (_lv >= 115, _lv)
                except Exception:
                    pass
                return (False, None)

            # ── filter_above_vwap_chase ENFORCED 2026-05-14 PM ─────────────
            # Blocks entries that are 10-30% ABOVE the 24h VWAP — classic
            # "chasing local strength" loser pattern. Mined on n=105 paired
            # (TRAIN -$0.52/tr, TEST -$1.31/tr — gets STRONGER on held-out).
            # 38-42% WR vs ~50% baseline.
            #
            # Mechanism: VWAP_h24 is the average price weighted by volume
            # over 24h. Entering at +10-30% above VWAP means buying at a
            # local high; mean reversion punishes these entries. Below VWAP
            # (-20% to 0%) is the sweet spot for dip entry.
            #
            # Carve-out: liq_velocity_h1>=115 big-buyer rescue.
            _filter_avc_block_reasons: list = []
            _avc_vwap = vwap_features.get("pct_above_vwap_h24") if isinstance(vwap_features, dict) else None
            if _avc_vwap is not None:
                try:
                    if 10.0 <= float(_avc_vwap) < 30.0:
                        _filter_avc_block_reasons.append(
                            f"pct_above_vwap_h24={float(_avc_vwap):+.1f}%∈[+10,+30) "
                            f"(chasing local strength)"
                        )
                except Exception:
                    pass
            _filter_avc_verdict = "BLOCK" if _filter_avc_block_reasons else "PASS"
            c[f"filter_above_vwap_chase_{_filter_avc_verdict.lower()}"] = c.get(
                f"filter_above_vwap_chase_{_filter_avc_verdict.lower()}", 0
            ) + 1
            _avc_rescued, _avc_lvh1 = _big_buyer_rescued()
            if _filter_avc_verdict == "BLOCK" and not _avc_rescued:
                logger.info(
                    f"[DipScanner] BLOCKED by filter_above_vwap_chase: "
                    f"{token_symbol} reasons={','.join(_filter_avc_block_reasons)}"
                )
                continue
            elif _filter_avc_verdict == "BLOCK" and _avc_rescued:
                logger.info(
                    f"[DipScanner] filter_above_vwap_chase rescued by big_buyer: "
                    f"{token_symbol} liq_velocity_h1=${_avc_lvh1:.0f}/txn>=115"
                )

            # ── filter_knife_catch_peak ENFORCED 2026-05-14 PM ─────────────
            # Blocks entries when h24_ratio_to_peak ∈ [0.85, 1.0) — token is
            # within 15% of its 24h high (knife-catching the local peak).
            # Mined on n=100 paired (TRAIN -$0.20/tr, TEST -$2.54/tr — held-
            # out shows 7% WR on n=14 — MUCH stronger held-out).
            #
            # Mechanism: entering at 85-100% of the 24h peak means the
            # token is at or very near its recent high. Memecoins at fresh
            # highs almost always fade as profit-taking kicks in. Sweet
            # spot for dip entry is [0.6, 0.85] (mid-fade, 56-58% WR).
            #
            # Carve-out: liq_velocity_h1>=115 big-buyer rescue.
            _filter_kcp_block_reasons: list = []
            try:
                _kcp_ratio = (pc_h24 / float(peak_h24_6h)) if float(peak_h24_6h or 0) > 0 else None
                if _kcp_ratio is not None and 0.85 <= _kcp_ratio < 1.0:
                    _filter_kcp_block_reasons.append(
                        f"h24_ratio_to_peak={_kcp_ratio:.2f}∈[0.85,1.0) "
                        f"(knife-catching local peak)"
                    )
            except Exception:
                pass
            _filter_kcp_verdict = "BLOCK" if _filter_kcp_block_reasons else "PASS"
            c[f"filter_knife_catch_peak_{_filter_kcp_verdict.lower()}"] = c.get(
                f"filter_knife_catch_peak_{_filter_kcp_verdict.lower()}", 0
            ) + 1
            _kcp_rescued, _kcp_lvh1 = _big_buyer_rescued()
            if _filter_kcp_verdict == "BLOCK" and not _kcp_rescued:
                logger.info(
                    f"[DipScanner] BLOCKED by filter_knife_catch_peak: "
                    f"{token_symbol} reasons={','.join(_filter_kcp_block_reasons)}"
                )
                continue
            elif _filter_kcp_verdict == "BLOCK" and _kcp_rescued:
                logger.info(
                    f"[DipScanner] filter_knife_catch_peak rescued by big_buyer: "
                    f"{token_symbol} liq_velocity_h1=${_kcp_lvh1:.0f}/txn>=115"
                )

            # ── filter_reviving_lifecycle ENFORCED 2026-05-14 PM (Commit B) ──
            # Block when lifecycle_stage == "reviving". Mined on n=36:
            # TRAIN -$1.03/tr, TEST -$0.94/tr — stable across both periods.
            # 33% WR vs ~50% baseline. The "reviving" stage classifier
            # captures tokens that pumped, dumped, and are mid-reattempt —
            # most fail. Distinct from active_runner (52% WR) which we keep.
            _filter_rvl_block_reasons: list = []
            _rvl_stage = _lifecycle_dict.get("lifecycle_stage") if isinstance(_lifecycle_dict, dict) else None
            if _rvl_stage == "reviving":
                _filter_rvl_block_reasons.append(
                    f"lifecycle_stage=reviving (failed-relaunch shape)"
                )
            _filter_rvl_verdict = "BLOCK" if _filter_rvl_block_reasons else "PASS"
            c[f"filter_reviving_lifecycle_{_filter_rvl_verdict.lower()}"] = c.get(
                f"filter_reviving_lifecycle_{_filter_rvl_verdict.lower()}", 0
            ) + 1
            _rvl_rescued, _rvl_lvh1 = _big_buyer_rescued()
            if _filter_rvl_verdict == "BLOCK" and not _rvl_rescued:
                logger.info(
                    f"[DipScanner] BLOCKED by filter_reviving_lifecycle: {token_symbol}"
                )
                continue
            elif _filter_rvl_verdict == "BLOCK" and _rvl_rescued:
                logger.info(
                    f"[DipScanner] filter_reviving_lifecycle rescued by big_buyer: "
                    f"{token_symbol} liq_velocity_h1=${_rvl_lvh1:.0f}/txn>=115"
                )

            # ── filter_already_mooned ENFORCED 2026-05-14 PM (Commit B) ───
            # Block when peak_h24_6h_pct >= 3000% (token has 30x+ in 6-24h).
            # Mined on n=30 (TRAIN -$0.71/tr, TEST -$0.95/tr, 40% WR).
            # Such tokens are post-mania and almost always fade.
            # Carve-out: liq_velocity_h1>=115 big-buyer rescue.
            _filter_am_block_reasons: list = []
            try:
                if float(peak_h24_6h or 0) >= 3000.0:
                    _filter_am_block_reasons.append(
                        f"peak_h24_6h={float(peak_h24_6h):.0f}%>=3000 "
                        f"(already-mooned, post-mania)"
                    )
            except Exception:
                pass
            _filter_am_verdict = "BLOCK" if _filter_am_block_reasons else "PASS"
            c[f"filter_already_mooned_{_filter_am_verdict.lower()}"] = c.get(
                f"filter_already_mooned_{_filter_am_verdict.lower()}", 0
            ) + 1
            _am_rescued, _am_lvh1 = _big_buyer_rescued()
            if _filter_am_verdict == "BLOCK" and not _am_rescued:
                logger.info(
                    f"[DipScanner] SHADOW filter_already_mooned would BLOCK: {token_symbol} "
                    f"peak_h24={float(peak_h24_6h):.0f}%"
                )
                # REVERTED to SHADOW 2026-05-16 PM. Universe audit
                # showed blocked cohort 76.7% WR / +9.59% EV vs
                # passed 72.1% WR / +5.98% EV (n_block=814). High pc_h24
                # is selecting the FRESH RUNNERS where dips recover —
                # not "already exhausted" tokens.
            elif _filter_am_verdict == "BLOCK" and _am_rescued:
                logger.info(
                    f"[DipScanner] filter_already_mooned rescued by big_buyer: "
                    f"{token_symbol} liq_velocity_h1=${_am_lvh1:.0f}/txn>=115"
                )

            # ── filter_stale_h1_peak ENFORCED 2026-05-14 PM (Commit B) ────
            # Block when time_since_h1_peak_secs ∈ [3000, 3600) — h1 peak
            # was 50-60 min ago. Mined on n=24 (TRAIN -$0.67/tr, TEST
            # -$1.15/tr, 33% WR). "Stale fade" — peak set near end of h1
            # window, by the time entry fires the move is exhausted.
            # Carve-out: liq_velocity_h1>=115 big-buyer rescue.
            _filter_shp_block_reasons: list = []
            _shp_ts = trajectory_features.get("time_since_h1_peak_secs") if isinstance(trajectory_features, dict) else None
            if _shp_ts is not None:
                try:
                    if 3000.0 <= float(_shp_ts) < 3600.0:
                        _filter_shp_block_reasons.append(
                            f"time_since_h1_peak={float(_shp_ts):.0f}s∈[3000,3600) "
                            f"(stale h1 peak — 50-60min fade)"
                        )
                except Exception:
                    pass
            _filter_shp_verdict = "BLOCK" if _filter_shp_block_reasons else "PASS"
            c[f"filter_stale_h1_peak_{_filter_shp_verdict.lower()}"] = c.get(
                f"filter_stale_h1_peak_{_filter_shp_verdict.lower()}", 0
            ) + 1
            _shp_rescued, _shp_lvh1 = _big_buyer_rescued()
            if _filter_shp_verdict == "BLOCK" and not _shp_rescued:
                logger.info(
                    f"[DipScanner] BLOCKED by filter_stale_h1_peak: {token_symbol} "
                    f"reasons={','.join(_filter_shp_block_reasons)}"
                )
                continue
            elif _filter_shp_verdict == "BLOCK" and _shp_rescued:
                logger.info(
                    f"[DipScanner] filter_stale_h1_peak rescued by big_buyer: "
                    f"{token_symbol} liq_velocity_h1=${_shp_lvh1:.0f}/txn>=115"
                )

            # ── Volume velocity features (2026-05-10) ──
            # Hypothesis: dips into rising-volume regimes win; dips into
            # decaying-volume regimes round-trip. We have vol_h1, vol_h6, and
            # vol_m5 from DexScreener — cheap derivations expose acceleration.
            #
            # vol_5m_per_hr_proj: extrapolated hourly rate from 5m vol
            # vol_h1_accel:       (vol_h1) / (vol_h6 / 6)   — h1 vs h6 baseline
            # vol_5m_burst:       (vol_5m * 12) / max(vol_h1, 1)
            #                     ratio of last 5m's projected hourly rate to
            #                     actual h1. >1.5 = volume accelerating
            #                     <0.5 = volume decaying mid-trade
            volume_velocity_features: dict = {}
            try:
                _vol_baseline_h6 = (vol_h6 / 6.0) if vol_h6 > 0 else 0.0
                _vol_h1_accel = (vol_h1 / _vol_baseline_h6) if _vol_baseline_h6 > 0 else None
                _vol_5m_proj_hr = vol_m5 * 12.0  # extrapolate 5m rate to hourly
                _vol_5m_burst = (_vol_5m_proj_hr / vol_h1) if vol_h1 > 0 else None
                volume_velocity_features = {
                    "vol_h1_accel_vs_h6": (round(_vol_h1_accel, 3)
                                           if _vol_h1_accel is not None else None),
                    "vol_5m_burst_vs_h1": (round(_vol_5m_burst, 3)
                                           if _vol_5m_burst is not None else None),
                    "vol_5m_proj_hr_usd": round(_vol_5m_proj_hr, 2),
                }
            except Exception:
                pass

            # ── Liquidity velocity (paper-derived, SHADOW 2026-05-12) ──
            # arxiv 2602.14860: "Fast accumulation of liquidity through a small
            # number of trades is the strongest predictor of graduation."
            # USD per txn is the simplest expression. Recorded shadow-only.
            try:
                _txn_m5_total = (b_m5 or 0) + (s_m5 or 0)
                _liq_vel_m5 = (vol_m5 / _txn_m5_total) if _txn_m5_total > 0 else None
                _txn_h1_total = (int((txns_h1 or {}).get("buys") or 0)
                                 + int((txns_h1 or {}).get("sells") or 0))
                _liq_vel_h1 = (vol_h1 / _txn_h1_total) if _txn_h1_total > 0 else None
                volume_velocity_features["liq_velocity_m5_usd_per_txn"] = (
                    round(_liq_vel_m5, 2) if _liq_vel_m5 is not None else None)
                volume_velocity_features["liq_velocity_h1_usd_per_txn"] = (
                    round(_liq_vel_h1, 2) if _liq_vel_h1 is not None else None)
            except Exception:
                pass

            # ── Shewhart 4-sigma dump scan (paper-derived, SHADOW 2026-05-12) ──
            # arxiv 2602.14860: "92.22% of failed tokens exhibit detectable dump
            # events via Shewhart control charts (4-sigma threshold on
            # log-returns)." Computes z-score of each 5m candle's log-return
            # against the 30-candle (2.5h) window. Records max negative z-score
            # and a boolean flag for z <= -4. Shadow only — no entry gating.
            shewhart_features: dict = {
                "shadow_shewhart_dump_detected": None,
                "shadow_shewhart_max_neg_z": None,
            }
            try:
                _cs5_shew = (_chart_data.candles_5m[-30:]
                             if _chart_data and _chart_data.candles_5m else [])
                if len(_cs5_shew) >= 10:
                    import math
                    _logrets = []
                    for i in range(1, len(_cs5_shew)):
                        c0 = _cs5_shew[i - 1].close
                        c1 = _cs5_shew[i].close
                        if c0 > 0 and c1 > 0:
                            _logrets.append(math.log(c1 / c0))
                    if len(_logrets) >= 8:
                        _mu = sum(_logrets) / len(_logrets)
                        _var = sum((x - _mu) ** 2 for x in _logrets) / len(_logrets)
                        _sd = _var ** 0.5
                        if _sd > 0:
                            _z_scores = [(x - _mu) / _sd for x in _logrets]
                            _min_z = min(_z_scores)
                            shewhart_features["shadow_shewhart_max_neg_z"] = round(_min_z, 3)
                            shewhart_features["shadow_shewhart_dump_detected"] = (_min_z <= -4.0)
                            if _min_z <= -4.0:
                                logger.info(
                                    f"[DipScanner] filter_shewhart_dump SHADOW would-block: "
                                    f"{token_symbol} reasons=log_return_z_score={_min_z:.2f}<=-4 "
                                    f"(insider/whale dump event detected in 2.5h window)"
                                )
            except Exception:
                pass

            # Fail-safe: ensure CNN vars exist even if chart_data path was skipped
            if "_cnn_pattern" not in dir():
                _cnn_pattern = None
                _cnn_pattern_conf = None
                _cnn_outcome_prob = None
            entry_meta_dict = {
                # Signal-fire wall-clock timestamp (ms). Trader.buy will
                # compute signal_to_fill_ms after on-chain confirmation.
                "signal_ts_ms": int(time.time() * 1000),
                # Which trigger fired. Added 2026-05-12 for per-trigger WR
                # tracking. Single trigger = name, multi = "name1_name2".
                "trigger_source": _trigger_source,
                "triggers_fired": list(_triggers_fired),
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
                # filter_macro_panic — SHADOW 2026-05-16 macro context gate.
                "filter_macro_panic_verdict": _filter_macro_panic_verdict,
                "filter_macro_panic_block_reasons": _filter_macro_panic_block_reasons,
                "filter_macro_panic_premium_rescue": _filter_macro_panic_premium_rescue,
                # Breakthrough-trigger fast-path flags (2026-05-16 PM).
                # EARLY is set after _tier3_features ready (line ~3070);
                # covers strong_orderflow + sustained_accumulation predicates.
                # LATE is set after all 6 trigger evals; covers all 8 (6+2).
                "breakthrough_early_match": _breakthrough_early_match,
                "breakthrough_late_match": _breakthrough_late_match,
                # Round-2 mining triggers (2026-05-17) — chart-pattern compounds.
                "trigger_swing_structure_rsi_match": _trigger_swing_structure_rsi_match,
                "trigger_swing_structure_rsi_reasons": _trigger_swing_structure_rsi_reasons,
                "trigger_channel_pos_swing_match": _trigger_channel_pos_swing_match,
                "trigger_channel_pos_swing_reasons": _trigger_channel_pos_swing_reasons,
                # Round-3 mining triggers (2026-05-17).
                "trigger_channel_hvn_match": _trigger_channel_hvn_match,
                "trigger_channel_hvn_reasons": _trigger_channel_hvn_reasons,
                "trigger_shape_wick_match": _trigger_shape_wick_match,
                "trigger_shape_wick_reasons": _trigger_shape_wick_reasons,
                "trigger_cnn_lp_match": _trigger_cnn_lp_match,
                "trigger_cnn_lp_reasons": _trigger_cnn_lp_reasons,
                # Round-4 mining triggers (2026-05-17).
                "trigger_clean_consec_ll_match": _trigger_clean_consec_ll_match,
                "trigger_clean_consec_ll_reasons": _trigger_clean_consec_ll_reasons,
                # Round-5 mining trigger (2026-05-17).
                "trigger_sweep_holder_liq_match": _trigger_sweep_holder_liq_match,
                "trigger_sweep_holder_liq_reasons": _trigger_sweep_holder_liq_reasons,
                # Round-6 mining trigger (2026-05-17).
                "trigger_clean_dip_trend_match": _trigger_clean_dip_trend_match,
                "trigger_clean_dip_trend_reasons": _trigger_clean_dip_trend_reasons,
                # young_active_dip (2026-05-17, universe-recorder mining).
                "trigger_young_active_dip_match": _trigger_young_active_dip_match,
                "trigger_young_active_dip_reasons": _trigger_young_active_dip_reasons,
                # V-bottom triggers (2026-05-17 PM, universe-recorder mining).
                "trigger_volatile_5m_dip_match": _trigger_volatile_5m_dip_match,
                "trigger_volatile_5m_dip_reasons": _trigger_volatile_5m_dip_reasons,
                "trigger_v_bottom_body_match": _trigger_v_bottom_body_match,
                "trigger_v_bottom_body_reasons": _trigger_v_bottom_body_reasons,
                # Round-2 deep-mine triggers (2026-05-17 PM, post-freshness fix).
                "trigger_volume_burst_runner_match": _trigger_volume_burst_runner_match,
                "trigger_volume_burst_runner_reasons": _trigger_volume_burst_runner_reasons,
                "trigger_volatile_buyer_dom_match": _trigger_volatile_buyer_dom_match,
                "trigger_volatile_buyer_dom_reasons": _trigger_volatile_buyer_dom_reasons,
                # Runner-predictive trigger (2026-05-17 PM, 3x PREMIUM).
                "trigger_fresh_runner_factory_match": _trigger_fresh_runner_factory_match,
                "trigger_fresh_runner_factory_reasons": _trigger_fresh_runner_factory_reasons,
                # Round-5 volume-push triggers (2026-05-18).
                "trigger_active_dip_match": _trigger_active_dip_match,
                "trigger_active_dip_reasons": _trigger_active_dip_reasons,
                "trigger_high_activity_runner_match": _trigger_high_activity_runner_match,
                "trigger_high_activity_runner_reasons": _trigger_high_activity_runner_reasons,
                "trigger_confirmed_dip_match": _trigger_confirmed_dip_match,
                "trigger_confirmed_dip_reasons": _trigger_confirmed_dip_reasons,
                "trigger_low_liq_active_dip_match": _trigger_low_liq_active_dip_match,
                "trigger_low_liq_active_dip_reasons": _trigger_low_liq_active_dip_reasons,
                # Round-6 anchor trigger (2026-05-18).
                "trigger_high_churn_microcap_match": _trigger_high_churn_microcap_match,
                "trigger_high_churn_microcap_reasons": _trigger_high_churn_microcap_reasons,
                # Trending-token flag (2026-05-18). Axiom Top+Trending feeds.
                "is_trending_token": _is_trending_token,
                # high_activity_fast_path (2026-05-17). Bypasses trader-side
                # filter_combo_v2/filter_chart_bear/filter_top10_holder_band.
                "high_activity_fast_path": _high_activity_fast_path,
                # filter_blowoff_top — ENFORCED 2026-05-16 PM (pc_h24>=500% block).
                "filter_blowoff_top_verdict": _filter_blowoff_top_verdict,
                "filter_blowoff_top_block_reasons": _filter_blowoff_block_reasons,
                "filter_blowoff_top_premium_rescue": _filter_blowoff_premium_rescue,
                # filter_high_activity_fomo — ENFORCED 2026-05-16 PM
                # (buys_per_min_recent >= 10 block — FOMO peak).
                "filter_high_activity_fomo_verdict": _filter_high_fomo_verdict,
                "filter_high_activity_fomo_block_reasons": _filter_high_fomo_block_reasons,
                # filter_post_pump_corpse — ENFORCED 2026-05-16 PM
                # (pc_h1>=+500% OR (pc_h24>=+200% AND buys_per_min<=2)).
                "filter_post_pump_corpse_verdict": _filter_post_pump_corpse_verdict,
                "filter_post_pump_corpse_block_reasons": _filter_corpse_pump_block_reasons,
                # filter_sweep_too_recent — ENFORCED 2026-05-13 anti-knife-catch.
                "filter_sweep_too_recent_verdict": _filter_sweep_too_recent_verdict,
                "filter_sweep_too_recent_block_reasons": _filter_sweep_too_recent_block_reasons,
                # filter_no_signatures — ENFORCED 2026-05-10 (0-of-6 sigs).
                "filter_no_signatures_verdict": _filter_no_signatures_verdict,
                "filter_no_signatures_block_reasons": _filter_no_sig_block_reasons,
                "filter_no_signatures_sigs_hit": _sigs_hit,
                "filter_no_signatures_sigs_available": _sigs_available,
                "filter_no_signatures_sigs_present": _sigs_present,
                # filter_dying_volume — SHADOW 2026-05-11. Block if pre-entry
                # 1s_vol_decay_120s < 0.30 (late-period vol <30% of early).
                "filter_dying_volume_verdict": _fdv_verdict,
                "filter_dying_volume_block_reasons": _fdv_reasons,
                # filter_solo_decay — ENFORCED 2026-05-11. Block solo trigger
                # entries on deeply-decayed old tokens (AVA8/ELIEN class).
                "filter_solo_decay_verdict": _fsd_verdict,
                "filter_solo_decay_block_reasons": _fsd_block_reasons,
                # filter_rsi_overbought — ENFORCED 2026-05-11. Block when 5m
                # RSI >= 50 (downside momentum has reset; not really an
                # oversold dip). Mined from 684 modern trades: rsi_5m<50
                # has 75% WR (CV) vs 58% baseline.
                "filter_rsi_overbought_verdict": _filter_rsi_overbought_verdict,
                "filter_rsi_overbought_block_reasons": _filter_rsi_overbought_block_reasons,
                # 1s base-formation features — SHADOW 2026-05-11 (no filtering).
                "1s_bars_60s": _1s_features.get("bars_60s"),
                "1s_bars_120s": _1s_features.get("bars_120s"),
                "1s_range_pct_60s": _1s_features.get("range_pct_60s"),
                "1s_red_count_60s": _1s_features.get("red_count_60s"),
                "1s_red_pct_60s": _1s_features.get("red_pct_60s"),
                "1s_close_pos_60s": _1s_features.get("close_pos_60s"),
                "1s_vol_decay_120s": _1s_features.get("vol_decay_120s"),
                # 1s_sweep_reject_detected — SHADOW 2026-05-11 (#4).
                # Bottom-catch signal: long lower wick + green close + high
                # volume in last 3 30S bars. Want forward W:L ratio on this.
                "1s_sweep_reject_detected": _1s_features.get("sweep_reject_detected"),
                "1s_sweep_reject_bar_idx": _1s_features.get("sweep_reject_bar_idx"),
                # 1s_cascade_reversal — SHADOW 2026-05-11 (#4b).
                # Wider bottom signal than sweep_reject: 5+ consecutive red 1s
                # bars followed by green reversal closing in top 30% of post-
                # cascade range. Targets Goblin-style multi-bar capitulation
                # bottoms that single-bar sweep_reject misses.
                "1s_cascade_length": _1s_features.get("cascade_length"),
                "1s_cascade_reversal_detected": _1s_features.get("cascade_reversal_detected"),
                "1s_cascade_reversal_close_pos": _1s_features.get("cascade_reversal_close_pos"),
                "1s_cascade_reversal_pct": _1s_features.get("cascade_reversal_pct"),
                # 1s_structural_stop_pct — SHADOW 2026-05-11 (#5).
                # Distance from entry close to recent 1s low + 0.5% buffer.
                # Compares to fixed dip_stop_pct=7 — if structural is tighter,
                # we're overpaying on stops; if looser, we're under-stopping
                # the volatile setups.
                "1s_structural_stop_pct": _1s_features.get("structural_stop_pct"),
                # 1s V-bottom microstructure — ENFORCED 2026-05-13.
                # Four new features that build the data foundation for the
                # 1s_v_bottom_strict trigger and bottom_score composite.
                "1s_green_run_end": _1s_features.get("green_run_end"),
                "1s_bars_since_low_60s": _1s_features.get("bars_since_low_60s"),
                "1s_lower_wick_ratio_last": _1s_features.get("lower_wick_ratio_last"),
                "1s_vol_burst_on_reversal_ratio": _1s_features.get("vol_burst_on_reversal_ratio"),
                # 1s_bottom_score: 0-100 composite weighted score from
                # all 1s microstructure signals. Used as a trigger gate.
                "1s_bottom_score": _1s_features.get("bottom_score"),
                # 1s_base_confirmed_at_entry — SHADOW 2026-05-11.
                # Derived boolean: would the active-confirmation gate have
                # entered this trade? Criteria (require all):
                #   bars_60s >= 2 (enough data), AND
                #   close_pos_60s >= 0.5 (close in upper half of range), AND
                #   red_pct_60s <= 0.5 (not majority red bars)
                # Fail-open (=True) if features missing (no 1s data fetched).
                # Validated on 6-trade today sample: would block 3/4 losers
                # (LAYER, TripleT, BABYTROLL), preserve both winners
                # (WOJAK, DATA). RAGE leaks. Will validate forward over ~50
                # trades before promoting from shadow to enforced.
                "1s_base_confirmed_at_entry": (
                    True if _1s_features.get("bars_60s") is None
                    else (
                        _1s_features.get("bars_60s", 0) >= 2
                        and (_1s_features.get("close_pos_60s") or 0) >= 0.5
                        and (_1s_features.get("red_pct_60s") or 0) <= 0.5
                    )
                ),
                # filter_chasing_bounce — ENFORCED 2026-05-10 (pc_m5 > +5%).
                "filter_chasing_bounce_verdict": _filter_chasing_bounce_verdict,
                "filter_chasing_bounce_block_reasons": _filter_chasing_bounce_block_reasons,
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
                # Defensive filters from loser mining — ENFORCED 2026-05-15.
                "filter_dead_5m_eve_wknd_verdict": _filter_dead_5m_eve_wknd_verdict,
                "filter_dead_5m_eve_wknd_block_reasons": _filter_dead_5m_eve_wknd_block_reasons,
                "filter_sat_eve_midliq_verdict": _filter_sat_eve_midliq_verdict,
                "filter_sat_eve_midliq_block_reasons": _filter_sat_eve_midliq_block_reasons,
                "filter_microcap_trap_verdict": _filter_microcap_trap_verdict,
                "filter_microcap_trap_block_reasons": _filter_microcap_trap_block_reasons,
                # filter_falling_knife — ENFORCED 2026-05-15 (mtf<=-1 + 1m red).
                "filter_falling_knife_verdict": _filter_falling_knife_verdict,
                "filter_falling_knife_block_reasons": _filter_falling_knife_block_reasons,
                # chart_cnn — SHADOW 2026-05-15 (pattern + outcome head)
                "cnn_pattern": _cnn_pattern,
                "cnn_pattern_conf": _cnn_pattern_conf,
                "cnn_outcome_prob": _cnn_outcome_prob,
                # chart_cluster — ENFORCED 2026-05-15 (rug filter on cluster 19)
                "cnn_cluster_id": _cnn_cluster_id if "_cnn_cluster_id" in dir() else None,
                # trigger_post_capit_breakout — ENFORCED 2026-05-15 with
                # carve-outs on filter_turn / filter_sweep_too_recent /
                # filter_chasing_top. Positive V-bottom reversal trigger.
                # trigger_strong_orderflow — ENFORCED 2026-05-15 (on-chain mining).
                # Compound: net_flow_60s_usd>0 AND chart_mtf_score>=1 AND bs_m5>=1.5.
                # Lifetime evidence: 8/8 wins, +$6.08 net, p < 0.001 vs baseline 34.5% WR.
                "trigger_strong_orderflow_match": _trigger_strong_orderflow_match,
                "trigger_strong_orderflow_reasons": _trigger_strong_orderflow_reasons,
                # trigger_sustained_accumulation — ENFORCED 2026-05-15 (on-chain).
                # Lifetime: 7/7 wins, +$5.47 net.
                "trigger_sustained_accum_match": _trigger_sustained_accum_match,
                "trigger_sustained_accum_reasons": _trigger_sustained_accum_reasons,
                # trigger_chart_quality_bottom — ENFORCED 2026-05-15 (chart+score+bot_score).
                # Lifetime: 6/7 wins, +$3.60 net.
                "trigger_chart_qual_bottom_match": _trigger_chart_qual_bottom_match,
                "trigger_chart_qual_bottom_reasons": _trigger_chart_qual_bottom_reasons,
                # trigger_buyer_momentum_burst — ENFORCED 2026-05-15 (active buyer bursts).
                # Lifetime: 8/11 wins (72.7%), +$3.00 net.
                "trigger_buyer_momentum_burst_match": _trigger_buyer_momentum_burst_match,
                "trigger_buyer_momentum_burst_reasons": _trigger_buyer_momentum_burst_reasons,
                # trigger_flow_reversal — ENFORCED 2026-05-15 (reversal from decline).
                # Lifetime: 9/11 wins (81.8%), +$4.87 net.
                "trigger_flow_reversal_match": _trigger_flow_reversal_match,
                "trigger_flow_reversal_reasons": _trigger_flow_reversal_reasons,
                # trigger_chart_score_reversal — ENFORCED 2026-05-15.
                # Lifetime: 8/9 wins (88.9%), +$4.29 net.
                "trigger_chart_reversal_match": _trigger_chart_reversal_match,
                "trigger_chart_reversal_reasons": _trigger_chart_reversal_reasons,
                # trigger_micro_pattern_confirmed — ENFORCED 2026-05-15 (techncial patterns).
                # Lifetime: 10/11 wins (90.9%), +$5.34 net.
                "trigger_micro_pattern_match": _trigger_micro_pattern_match,
                "trigger_micro_pattern_reasons": _trigger_micro_pattern_reasons,
                # trigger_volume_profile_aligned — ENFORCED 2026-05-15 (POC value entry).
                # Lifetime: 8/8 wins (100%), +$4.67 net.
                "trigger_vp_aligned_match": _trigger_vp_aligned_match,
                "trigger_vp_aligned_reasons": _trigger_vp_aligned_reasons,
                # trigger_quiet_1s_buyer_dominance — ENFORCED 2026-05-15.
                # Lifetime: 10/12 wins (83.3%), +$4.46 net.
                "trigger_quiet_buyer_match": _trigger_quiet_buyer_match,
                "trigger_quiet_buyer_reasons": _trigger_quiet_buyer_reasons,
                # anti_pattern_suppression — ENFORCED 2026-05-15.
                # Clears all triggers when in 0/7 lifetime loss cohort.
                "anti_pattern_suppress_reason": _suppress_reason,
                "trigger_post_capit_breakout_match": _trigger_post_capit_breakout_match,
                "trigger_post_capit_breakout_reasons": _trigger_post_capit_breakout_reasons,
                "filter_low_volatility_block_reasons": _filter_low_vol_block_reasons,
                # filter_clean_break_p90 — ENFORCED 2026-05-13 (clean_break-specific
                # drift gate, p90_body<=5%).
                "filter_clean_break_p90_verdict": _filter_cb_p90_verdict,
                "filter_clean_break_p90_block_reasons": _filter_cb_p90_block_reasons,
                # filter_high_regime_buyvol — ENFORCED 2026-05-13 (high_regime-specific
                # absorption gate, buyvol_ratio_60m<=1.0).
                "filter_high_regime_buyvol_verdict": _filter_hr_buyvol_verdict,
                "filter_high_regime_buyvol_block_reasons": _filter_hr_buyvol_block_reasons,
                # trigger_extreme_sweep_1m — ENFORCED 2026-05-13 PM (deep candle
                # synthesis output: lw/body>=10 on 1m + peak_h24>=200 gate).
                "trigger_extreme_sweep_1m_match": _trigger_extreme_sweep_1m_match,
                "trigger_extreme_sweep_1m_reasons": _trigger_extreme_sweep_1m_reasons,
                # trigger_controlled_greens_5m — ENFORCED 2026-05-13 PM (>=4 of
                # last 8 5m bars normal-green + peak_h24>=200 gate).
                "trigger_controlled_greens_5m_match": _trigger_controlled_greens_5m_match,
                "trigger_controlled_greens_5m_reasons": _trigger_controlled_greens_5m_reasons,
                # trigger_pullback_in_uptrend — ENFORCED 2026-05-13 PM (round-2,
                # 1h_3green>=2 AND 5m_5green<=2 AND last_5m_green).
                "trigger_pullback_in_uptrend_match": _trigger_pullback_in_uptrend_match,
                "trigger_pullback_in_uptrend_reasons": _trigger_pullback_in_uptrend_reasons,
                # trigger_vol_surge_recent — ENFORCED 2026-05-13 PM (round-2,
                # recent_8h_vol_avg / prior_40h_vol_avg >= 3).
                "trigger_vol_surge_recent_match": _trigger_vol_surge_recent_match,
                "trigger_vol_surge_recent_reasons": _trigger_vol_surge_recent_reasons,
                # trigger_bullish_engulfing_5m — ENFORCED 2026-05-13 PM (round-3,
                # 5m bullish engulfing pattern, 100% precision on n=55 paired).
                "trigger_bullish_engulfing_5m_match": _trigger_bullish_engulfing_5m_match,
                "trigger_bullish_engulfing_5m_reasons": _trigger_bullish_engulfing_5m_reasons,
                # filter_1h_v_bottom_fake_recovery — ENFORCED 2026-05-13 PM
                # (round-5, blocks 1h v-bottom recovery setups; 0W/4L on n=55).
                "filter_1h_v_bottom_verdict": _filter_v_bottom_verdict,
                "filter_1h_v_bottom_block_reasons": _filter_v_bottom_block_reasons,
                # trigger_mtf_aligned_demand — ENFORCED 2026-05-13 PM (round-6,
                # chart_mtf_score>=0.5 + 1s_close_pos_60s>=0.7; 4W/0L on n=29).
                "trigger_mtf_aligned_demand_match": _trigger_mtf_aligned_demand_match,
                "trigger_mtf_aligned_demand_reasons": _trigger_mtf_aligned_demand_reasons,
                # trigger_liq_velocity_big_buyers — ENFORCED 2026-05-13 PM
                # (round-7, liq_velocity_h1>=$135/txn; 6W/0L on n=29).
                "trigger_liq_velocity_match": _trigger_liq_velocity_match,
                "trigger_liq_velocity_reasons": _trigger_liq_velocity_reasons,
                # trigger_net_flow_5m_demand — ENFORCED 2026-05-13 PM (round-7,
                # net_flow_5m_usd>=$300; 5W/0L on n=29).
                "trigger_net_flow_5m_match": _trigger_net_flow_5m_match,
                "trigger_net_flow_5m_reasons": _trigger_net_flow_5m_reasons,
                # trigger_mcap_psych_level — ENFORCED 2026-05-13 PM (round-7,
                # mcap within 5% of $1M/$2M/$5M/$10M/etc; 5W/0L on n=29).
                "trigger_mcap_psych_match": _trigger_mcap_psych_match,
                "trigger_mcap_psych_reasons": _trigger_mcap_psych_reasons,
                # trigger_whale_conviction — ENFORCED 2026-05-14 PM (Commit C,
                # 35-angle mining): whale_buy_present_2k OR
                # top10_buyer_within_60s_count>=3.
                "trigger_whale_conviction_match": _trigger_whale_conviction_match,
                "trigger_whale_conviction_reasons": _trigger_whale_conviction_reasons,
                # trigger_strong_uptrend_dip — ENFORCED 2026-05-14 PM (Compound D
                # from chart inspection, 100% precision n=4): 1h_6h_chg>30 AND
                # 1h_4_red<=1.
                "trigger_strong_uptrend_dip_match": _trigger_strong_uptrend_dip_match,
                "trigger_strong_uptrend_dip_reasons": _trigger_strong_uptrend_dip_reasons,
                # trigger_modest_pump_deep_retrace — ENFORCED 2026-05-14 PM.
                # MASCOTS pattern: peak[50,150) AND ratio<0.10. Audit n=6, 66.7% WR.
                "trigger_modest_pump_deep_retrace_match": _trigger_modest_pump_deep_retrace_match,
                "trigger_modest_pump_deep_retrace_reasons": _trigger_modest_pump_deep_retrace_reasons,
                # trigger_small_pump_shallow_retrace — ENFORCED 2026-05-14 PM.
                # Highest-EV cohort: peak[25,50) AND ratio[0.60,0.80). Audit n=56,
                # 66.1% WR, +$418.8 total ($7.48/trade avg).
                "trigger_small_pump_shallow_retrace_match": _trigger_small_pump_shallow_retrace_match,
                "trigger_small_pump_shallow_retrace_reasons": _trigger_small_pump_shallow_retrace_reasons,
                # 5 exhaustive-mining triggers — ENFORCED 2026-05-14 PM.
                "trigger_shallow_retrace_fresh_pump_match": _trigger_shallow_retrace_fresh_pump_match,
                "trigger_shallow_retrace_fresh_pump_reasons": _trigger_shallow_retrace_fresh_pump_reasons,
                "trigger_midcap_quality_accumulation_match": _trigger_midcap_quality_accumulation_match,
                "trigger_midcap_quality_accumulation_reasons": _trigger_midcap_quality_accumulation_reasons,
                "trigger_fresh_graduate_buyers_match": _trigger_fresh_graduate_buyers_match,
                "trigger_fresh_graduate_buyers_reasons": _trigger_fresh_graduate_buyers_reasons,
                "trigger_small_pump_fresh_cycles_match": _trigger_small_pump_fresh_cycles_match,
                "trigger_small_pump_fresh_cycles_reasons": _trigger_small_pump_fresh_cycles_reasons,
                "trigger_midcap_bigpump_fresh_match": _trigger_midcap_bigpump_fresh_match,
                "trigger_midcap_bigpump_fresh_reasons": _trigger_midcap_bigpump_fresh_reasons,
                # Overnight-edge triggers — ENFORCED 2026-05-14 (mine_overnight_cohorts).
                "trigger_overnight_modest_pump_consol_match": _trigger_overnight_modest_pump_consol_match,
                "trigger_overnight_modest_pump_consol_reasons": _trigger_overnight_modest_pump_consol_reasons,
                "trigger_overnight_quiet_accumulation_match": _trigger_overnight_quiet_accumulation_match,
                "trigger_overnight_quiet_accumulation_reasons": _trigger_overnight_quiet_accumulation_reasons,
                "trigger_overnight_fresh_small_pump_match": _trigger_overnight_fresh_small_pump_match,
                "trigger_overnight_fresh_small_pump_reasons": _trigger_overnight_fresh_small_pump_reasons,
                "trigger_overnight_quality_old_match": _trigger_overnight_quality_old_match,
                "trigger_overnight_quality_old_reasons": _trigger_overnight_quality_old_reasons,
                "trigger_overnight_micropump_buyers_match": _trigger_overnight_micropump_buyers_match,
                "trigger_overnight_micropump_buyers_reasons": _trigger_overnight_micropump_buyers_reasons,
                "trigger_overnight_mature_midcap_match": _trigger_overnight_mature_midcap_match,
                "trigger_overnight_mature_midcap_reasons": _trigger_overnight_mature_midcap_reasons,
                "trigger_overnight_3d_bigpump_fresh_age_match": _trigger_overnight_3d_bigpump_fresh_age_match,
                "trigger_overnight_3d_bigpump_fresh_age_reasons": _trigger_overnight_3d_bigpump_fresh_age_reasons,
                "trigger_overnight_3d_bigpump_midcap_match": _trigger_overnight_3d_bigpump_midcap_match,
                "trigger_overnight_3d_bigpump_midcap_reasons": _trigger_overnight_3d_bigpump_midcap_reasons,
                "trigger_overnight_3d_midcap_liq_band_match": _trigger_overnight_3d_midcap_liq_band_match,
                "trigger_overnight_3d_midcap_liq_band_reasons": _trigger_overnight_3d_midcap_liq_band_reasons,
                "trigger_overnight_3d_bigpump_avgtrade_match": _trigger_overnight_3d_bigpump_avgtrade_match,
                "trigger_overnight_3d_bigpump_avgtrade_reasons": _trigger_overnight_3d_bigpump_avgtrade_reasons,
                "trigger_overnight_3d_midcap_mature_cycles_match": _trigger_overnight_3d_midcap_mature_cycles_match,
                "trigger_overnight_3d_midcap_mature_cycles_reasons": _trigger_overnight_3d_midcap_mature_cycles_reasons,
                # ─── 11 full-day 3D triggers — ENFORCED 2026-05-15 ───
                "trigger_3d_balanced_h1_fresh_predawn_match": _trigger_3d_balanced_h1_fresh_predawn_match,
                "trigger_3d_balanced_h1_fresh_predawn_reasons": _trigger_3d_balanced_h1_fresh_predawn_reasons,
                "trigger_3d_small_pump_shallow_fresh_match": _trigger_3d_small_pump_shallow_fresh_match,
                "trigger_3d_small_pump_shallow_fresh_reasons": _trigger_3d_small_pump_shallow_fresh_reasons,
                "trigger_3d_active_5m_small_pump_fresh_match": _trigger_3d_active_5m_small_pump_fresh_match,
                "trigger_3d_active_5m_small_pump_fresh_reasons": _trigger_3d_active_5m_small_pump_fresh_reasons,
                "trigger_3d_compound_buyers_fresh_age_match": _trigger_3d_compound_buyers_fresh_age_match,
                "trigger_3d_compound_buyers_fresh_age_reasons": _trigger_3d_compound_buyers_fresh_age_reasons,
                "trigger_3d_strong_h1_fresh_daytime_match": _trigger_3d_strong_h1_fresh_daytime_match,
                "trigger_3d_strong_h1_fresh_daytime_reasons": _trigger_3d_strong_h1_fresh_daytime_reasons,
                "trigger_3d_midrange_midcap_predawn_match": _trigger_3d_midrange_midcap_predawn_match,
                "trigger_3d_midrange_midcap_predawn_reasons": _trigger_3d_midrange_midcap_predawn_reasons,
                "trigger_3d_bigpump_midcap_24_7_match": _trigger_3d_bigpump_midcap_24_7_match,
                "trigger_3d_bigpump_midcap_24_7_reasons": _trigger_3d_bigpump_midcap_24_7_reasons,
                "trigger_3d_compound_midcap_fresh_age_match": _trigger_3d_compound_midcap_fresh_age_match,
                "trigger_3d_compound_midcap_fresh_age_reasons": _trigger_3d_compound_midcap_fresh_age_reasons,
                "trigger_3d_extreme_h1_midliq_predawn_match": _trigger_3d_extreme_h1_midliq_predawn_match,
                "trigger_3d_extreme_h1_midliq_predawn_reasons": _trigger_3d_extreme_h1_midliq_predawn_reasons,
                "trigger_3d_compound_strong5m_midtrade_match": _trigger_3d_compound_strong5m_midtrade_match,
                "trigger_3d_compound_strong5m_midtrade_reasons": _trigger_3d_compound_strong5m_midtrade_reasons,
                "trigger_3d_mature_midcap_postmidnight_match": _trigger_3d_mature_midcap_postmidnight_match,
                "trigger_3d_mature_midcap_postmidnight_reasons": _trigger_3d_mature_midcap_postmidnight_reasons,
                # ─── 8 deep-mining 3D triggers (WR>=80%) — ENFORCED 2026-05-15 ───
                "trigger_3d_liq_midcap_compound_match": _trigger_3d_liq_midcap_compound_match,
                "trigger_3d_liq_midcap_compound_reasons": _trigger_3d_liq_midcap_compound_reasons,
                "trigger_3d_h6_fresh_age_compound_match": _trigger_3d_h6_fresh_age_compound_match,
                "trigger_3d_h6_fresh_age_compound_reasons": _trigger_3d_h6_fresh_age_compound_reasons,
                "trigger_3d_h1_midcap_liq_24_7_match": _trigger_3d_h1_midcap_liq_24_7_match,
                "trigger_3d_h1_midcap_liq_24_7_reasons": _trigger_3d_h1_midcap_liq_24_7_reasons,
                "trigger_3d_h6_smallpump_midtrade_match": _trigger_3d_h6_smallpump_midtrade_match,
                "trigger_3d_h6_smallpump_midtrade_reasons": _trigger_3d_h6_smallpump_midtrade_reasons,
                "trigger_3d_h6_strong5m_old_match": _trigger_3d_h6_strong5m_old_match,
                "trigger_3d_h6_strong5m_old_reasons": _trigger_3d_h6_strong5m_old_reasons,
                "trigger_3d_h6_midcap_deepdrop_match": _trigger_3d_h6_midcap_deepdrop_match,
                "trigger_3d_h6_midcap_deepdrop_reasons": _trigger_3d_h6_midcap_deepdrop_reasons,
                "trigger_3d_bigpump_midcap_compound_match": _trigger_3d_bigpump_midcap_compound_match,
                "trigger_3d_bigpump_midcap_compound_reasons": _trigger_3d_bigpump_midcap_compound_reasons,
                "trigger_3d_midcap_fresh_age_compound_match": _trigger_3d_midcap_fresh_age_compound_match,
                "trigger_3d_midcap_fresh_age_compound_reasons": _trigger_3d_midcap_fresh_age_compound_reasons,
                # SHADOW 2026-05-14 PM — cascade-V-bottom catcher. BURNIE-grounded.
                "trigger_cascade_v_bottom_match": _trigger_cascade_v_bottom_match,
                "trigger_cascade_v_bottom_reasons": _trigger_cascade_v_bottom_reasons,
                # filter_mtf_strong_downtrend — ENFORCED 2026-05-13 PM (round-7,
                # blocks chart_mtf_score<=-2; 0W/5L on n=29).
                "filter_mtf_strong_downtrend_verdict": _filter_mtf_dn_verdict,
                "filter_mtf_strong_downtrend_block_reasons": _filter_mtf_dn_block_reasons,
                # filter_negative_net_flow_5m — ENFORCED 2026-05-14 AM
                # (blocks net_flow_5m_usd<0; +$19.66 lifetime save on n=34).
                "filter_negative_net_flow_5m_verdict": _filter_neg_nf5m_verdict,
                "filter_negative_net_flow_5m_block_reasons": _filter_neg_nf5m_block_reasons,
                # filter_above_vwap_chase — ENFORCED 2026-05-14 PM (blocks
                # pct_above_vwap_h24 ∈ [+10, +30); n=105 lifetime, stronger
                # held-out at -$1.31/tr vs -$0.52 train).
                "filter_above_vwap_chase_verdict": _filter_avc_verdict,
                "filter_above_vwap_chase_block_reasons": _filter_avc_block_reasons,
                # filter_knife_catch_peak — ENFORCED 2026-05-14 PM (blocks
                # h24_ratio_to_peak ∈ [0.85, 1.0); n=100 lifetime, 7% WR on
                # n=14 held-out test).
                "filter_knife_catch_peak_verdict": _filter_kcp_verdict,
                "filter_knife_catch_peak_block_reasons": _filter_kcp_block_reasons,
                # filter_reviving_lifecycle — ENFORCED 2026-05-14 PM Commit B.
                "filter_reviving_lifecycle_verdict": _filter_rvl_verdict,
                "filter_reviving_lifecycle_block_reasons": _filter_rvl_block_reasons,
                # filter_already_mooned — ENFORCED 2026-05-14 PM Commit B.
                "filter_already_mooned_verdict": _filter_am_verdict,
                "filter_already_mooned_block_reasons": _filter_am_block_reasons,
                # filter_stale_h1_peak — ENFORCED 2026-05-14 PM Commit B.
                "filter_stale_h1_peak_verdict": _filter_shp_verdict,
                "filter_stale_h1_peak_block_reasons": _filter_shp_block_reasons,
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
                "filter_quote_asymmetry_verdict": _filter_quote_asymmetry_verdict,
                "filter_quote_asymmetry_block_reasons": _filter_quote_asymmetry_block_reasons,
                "filter_15s_dump_verdict": _filter_15s_dump_verdict,
                "filter_15s_dump_block_reasons": _filter_15s_dump_block_reasons,
                "filter_5m_downtrend_verdict": _filter_5m_downtrend_verdict,
                "filter_5m_downtrend_block_reasons": _filter_5m_downtrend_block_reasons,
                "filter_lower_low_verdict": _filter_lower_low_verdict,
                "filter_lower_low_block_reasons": _filter_lower_low_block_reasons,
                "filter_lp_drain_verdict": _filter_lp_drain_verdict,
                "filter_lp_drain_block_reasons": _filter_lp_drain_block_reasons,
                "filter_buyer_fomo_verdict": _filter_buyer_fomo_verdict,
                "filter_buyer_fomo_block_reasons": _filter_buyer_fomo_block_reasons,
                "filter_slip_asym_verdict": _filter_slip_asym_verdict,
                "filter_slip_asym_block_reasons": _filter_slip_asym_block_reasons,
                "filter_regime_panic_verdict": _filter_regime_panic_verdict,
                "filter_regime_panic_block_reasons": _filter_regime_panic_block_reasons,
                "filter_dev_dumping_verdict": _filter_dev_dumping_verdict,
                "filter_dev_dumping_block_reasons": _filter_dev_dumping_block_reasons,
                # 3 SHADOW filters from regret analysis (2026-05-05 PM).
                "filter_bs_m5_low_verdict": _filter_bs_m5_low_verdict,
                "filter_bs_m5_low_block_reasons": _filter_bs_m5_low_block_reasons,
                # filter_bs_m5_weak — enforced 2026-05-12. Blocks bs_m5<1.0
                # when unique_buyers<12 AND net_flow_15s<4 (no rescue).
                "filter_bs_m5_weak_verdict": _filter_bs_m5_weak_verdict,
                "filter_bs_m5_weak_block_reasons": _filter_bs_m5_weak_block_reasons,
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
                                # — also includes Jito MEV tip-floor fields
                                # (jito_tip_floor_lamports, jito_tip_p99_lamports,
                                # jito_tip_p50_lamports) folded in above.
                **smart_money_features,  # smart_buys_5m_count/total_sol/seconds_ago
                                         # populated by AxiomSmartWalletTracker → registry
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
                **volume_velocity_features,  # vol_h1_accel_vs_h6, vol_5m_burst_vs_h1,
                                              # liq_velocity_m5/h1 (paper, SHADOW)
                **shewhart_features,  # shadow_shewhart_dump_detected/max_neg_z (paper, SHADOW)
                # Macro price-change snapshot at signal-fire. Computed at the
                # top of this iteration from pair.priceChange. Stamping into
                # entry_meta_dict so post-trade audit can mine on these axes.
                # Previously absent — broke pc_h6-using mining (flow_reversal,
                # chart_score_reversal triggers still fire because they use the
                # local variables; this field is for analytics only).
                "pc_h24": float(pc_h24) if pc_h24 is not None else None,
                "pc_h6": float(pc_h6) if pc_h6 is not None else None,
                "pc_h1": float(pc_h1) if pc_h1 is not None else None,
                "pc_m5": float(pc_m5) if pc_m5 is not None else None,
                # txn counts — added 2026-05-16 PM for calm_seller mining.
                # Universe finding: sells_h1<=411 AND mcap>=$531k → 100%
                # loose-WR on n=192. Stamp so forward audit can validate.
                "buys_h1": int(b_h1) if isinstance(b_h1, (int, float)) else None,
                "sells_h1": int(s_h1) if isinstance(s_h1, (int, float)) else None,
                # carve-out flags — was this entry rescued by an mtf_dn carve?
                # (filter block always runs before entry_meta is built, so the
                # variables are guaranteed defined at this point.)
                "mtf_dn_calm_seller_carve": bool(_mtf_dn_calm_seller_carve),
                "mtf_dn_pc_h1_carve": bool(_mtf_dn_pc_h1_carve),
            }

            # fusion_constrained — SHADOW 2026-05-15. 14-feature LR (chart MTF +
            # on-chain holders/LP + CNN cluster + 1m action + regime), trained
            # on lifetime closed paired trades with LOO-CV. Stamp P(win) into
            # entry_meta_dict so future analysis can correlate the shadow
            # probability against realized P&L. Fail-quiet on any error —
            # must not block buy.
            try:
                from models.fusion_constrained import get_fusion_constrained
                _fc_inf = get_fusion_constrained()
                if not _fc_inf.disabled:
                    from datetime import datetime, timezone
                    entry_meta_dict["fusion_constrained_score_shadow"] = (
                        _fc_inf.score_from_entry_meta(
                            entry_meta_dict,
                            time_iso=datetime.now(timezone.utc).isoformat(),
                        )
                    )
                else:
                    entry_meta_dict["fusion_constrained_score_shadow"] = None
            except Exception as _e:
                logger.debug(f"[DipScanner] fusion_constrained err: {_e}")
                entry_meta_dict["fusion_constrained_score_shadow"] = None

            # fusion_v2 — SHADOW 2026-05-16 PM. 12-feature regularized LR
            # (C=0.1) with median imputation. 10-fold CV AUC mean=0.737
            # (std=0.240) on n=90 paired trades. Beats v1 (LOO-AUC 0.59).
            # Features focus on bottom-detection axes: n_swing_lows_found,
            # 1s_bottom_score, chart_mtf_score, p90_buy_size_usd. Negative
            # coefficient on buys_per_min_recent (matches today's reverted
            # filter_high_activity_fomo finding).
            #
            # SHADOW only — does not gate. Audit forward 5-7 days to
            # validate before any threshold-based promotion.
            try:
                from models.fusion_v2 import get_fusion_v2
                _fv2 = get_fusion_v2()
                if not _fv2.disabled:
                    entry_meta_dict["fusion_v2_score_shadow"] = (
                        _fv2.score_from_entry_meta(entry_meta_dict)
                    )
                else:
                    entry_meta_dict["fusion_v2_score_shadow"] = None
            except Exception as _e:
                logger.debug(f"[DipScanner] fusion_v2 err: {_e}")
                entry_meta_dict["fusion_v2_score_shadow"] = None

            # Forward dataset — buy-level snapshot with full entry_meta.
            # Future fusion meta-models train on this dataset paired with the
            # outcome that gets stamped when the trade closes (via trader).
            try:
                if _chart_data:
                    from feeds.forward_dataset_collector import get_collector as _get_fwd
                    from datetime import datetime as _dt, timezone as _tz
                    _get_fwd().dump_buy_snapshot(
                        token_address=token_address,
                        ts_iso=_dt.now(_tz.utc).isoformat(),
                        candles_1m=_chart_data.candles_1m or [],
                        candles_5m=_chart_data.candles_5m or [],
                        candles_15m=_chart_data.candles_15m or [],
                        entry_meta=entry_meta_dict,
                    )
            except Exception as _e:
                logger.debug(f"[DipScanner] buy_snapshot err: {_e}")

            # ── Position sizing tier — ENFORCED 2026-05-16 PM ──────────────
            # Three tiers based on premium signature + trigger composition:
            #
            #   PREMIUM (2x base): premium compound met
            #     (avg_trade_size_h1_usd>=116 AND liq_velocity_h1>=135
            #      AND p90_buy_size_usd>=153)
            #     Past data: 79-100% WR on these trades.
            #
            #   MARGINAL (0.5x base): all fired triggers are in the marginal
            #     set (chronic underperformers w/o premium).
            #     filter_premium_required already blocks the worst of these,
            #     but if any escapes (e.g. data missing on a premium feature),
            #     size down for risk control.
            #
            #   STANDARD (1x base): everything else (strong compound, mixed).
            #
            # Risk management: even if a brand-new signature (e.g.
            # fresh_pump_retrace) is over-fitted to mining data, the marginal
            # tier limits per-trade blowup to ~$10 vs ~$20 standard.
            _MARGINAL_FOR_SIZE = {
                "patient_bottom", "informed_cluster", "1s_capit_reversal",
                "whale_conviction", "grad_window_dip", "alpha_buyperscold",
                "net_flow_5m_demand", "fresh_pump_retrace",
            }
            _ats_size = float(avg_trade_size_h1) if avg_trade_size_h1 else None
            try:
                _lv_size = volume_velocity_features.get("liq_velocity_h1_usd_per_txn")
            except (NameError, AttributeError):
                _lv_size = None
            try:
                _p90_size = _trade_log_dict.get("p90_buy_size_usd")
            except (NameError, AttributeError):
                _p90_size = None
            _is_premium_size = (
                _ats_size is not None and _ats_size >= 116
                and _lv_size is not None and _lv_size >= 135
                and _p90_size is not None and _p90_size >= 153
            )
            # Premium-equivalent: chart_quality_bottom + chart_score_reversal pair.
            # 7d our-trade audit: 75% WR n=4 (+$0.94 total). Both fire on a
            # high-confidence chart reversal pattern — promote to premium tier.
            # Added 2026-05-16 PM.
            _chart_pair_premium = (
                "chart_quality_bottom" in _triggers_fired
                and "chart_score_reversal" in _triggers_fired
            )
            if _chart_pair_premium:
                _is_premium_size = True
            # 2026-05-17 PM — v_bottom_body trigger gets premium size.
            # Universe-recorder n=176/day, 78.4% WR5, +10.18% rpnl. High-precision
            # V-bottom signature warrants 2x size ($40 vs $20 standard).
            if "v_bottom_body" in _triggers_fired:
                _is_premium_size = True
            _all_marginal_size = (
                bool(_triggers_fired)
                and all(t in _MARGINAL_FOR_SIZE for t in _triggers_fired)
            )
            # ── sol_micro_uptick sizing modifier — ENFORCED 2026-05-16 PM ───
            # When SOL is ticking UP in the 1 min before entry, broader market
            # is risk-on; memecoin entries do better. Mined Cohen's d=+0.79
            # (strongest macro feature) on n=88 30d paired trades:
            #   sol_pc_m1 >= +0.01: n=12, 42% WR (vs 26% baseline)
            #   sol_pc_m1 <  +0.01: ~25% WR
            # Sample is small but effect size large. Shipped as STANDARD→
            # macro_up sizing boost (1.5x) rather than a hard filter, so
            # we don't kill volume on a small-sample signal. Doesn't override
            # premium (which is already 2x) or marginal (0.5x — risk gated).
            try:
                _sol_uptick_m1 = sol_features.get("sol_pc_m1")
            except (NameError, AttributeError):
                _sol_uptick_m1 = None
            _sol_micro_uptick = (
                isinstance(_sol_uptick_m1, (int, float))
                and _sol_uptick_m1 >= 0.01
            )
            # 2026-05-17 PM — premium_runner tier (3x). Reserved for
            # trigger_fresh_runner_factory: universe-recorder mining showed
            # 69% P(peak>=20%), 44% P(peak>=50%) on this n=71/day cohort.
            # 3x size justified by expected +18%/trade rpnl under asymmetric
            # exit ladder.
            _is_premium_runner = "fresh_runner_factory" in _triggers_fired
            if _is_premium_runner:
                _position_size = self.position_usd * 3.0
                _size_tier = "premium_runner"
            elif _is_premium_size:
                _position_size = self.position_usd * 2.0
                _size_tier = "premium"
            elif _all_marginal_size:
                _position_size = self.position_usd * 0.5
                _size_tier = "marginal"
            elif _sol_micro_uptick:
                _position_size = self.position_usd * 1.5
                _size_tier = "macro_up"
            else:
                _position_size = self.position_usd
                _size_tier = "standard"
            logger.info(
                f"[DipScanner] Position size tier: {_size_tier} ${_position_size:.0f} "
                f"(base ${self.position_usd:.0f}) for {token_symbol} triggers={_triggers_fired}"
                + (f" sol_pc_m1={_sol_uptick_m1:+.3f}" if _sol_uptick_m1 is not None else "")
            )

            await self.trader.buy(
                token_address=token_address,
                token_symbol=token_symbol,
                chain_id="solana",
                override_usd=_position_size,
                reason=(
                    f"dip_buy [{_size_tier}]: 24h={pc_h24:+.1f}% 1h={pc_h1:+.1f}% 5m={pc_m5:+.1f}% "
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
                "filter_falling_knife_block",
                "filter_sweep_too_recent_block", "filter_rsi_overbought_block",
                "filter_round_trip_block", "filter_weak_bounce_v2_block",
                "filter_quote_asymmetry_block", "filter_15s_dump_block",
                "filter_5m_downtrend_block", "filter_lower_low_block",
                "filter_lp_drain_block", "filter_buyer_fomo_block",
                "filter_fofar_block",
                "filter_vp_poc_block",
                "filter_two_pattern_block",
                # 4 SHADOW filters added 2026-05-05 — counters only, no enforcement.
                "filter_weak_bounce_block", "filter_slip_asym_block",
                "filter_regime_panic_block", "filter_dev_dumping_block",
                # 3 SHADOW filters from regret analysis (2026-05-05 PM).
                "filter_bs_m5_low_block", "filter_big_trade_size_block",
                "filter_stale_watch_block",
                # ENFORCED 2026-05-12 — surgical bs_m5<1.0 no-rescue block.
                "filter_bs_m5_weak_block",
                # Timing fix shadow 2026-05-05 PM.
                "filter_confirmation_candle_block",
                # ENFORCED 2026-05-06 — clean-break user pattern.
                "filter_clean_break_block",
                # ENFORCED 2026-05-06 PM — double-bearish-context gate.
                "filter_double_bear_block",
                # ENFORCED 2026-05-06 PM — bs_m5<0.50 single-axis gate.
                "filter_seller_dominant_block",
                # ENFORCED 2026-05-10 — 0-of-6 winner-signatures gate.
                "filter_no_signatures_block",
                # ENFORCED 2026-05-10 — chasing-bounce gate (pc_m5 > +5%).
                "filter_chasing_bounce_block",
                # ENFORCED 2026-05-12 — dead-token gate (p90_body<1.0%).
                "filter_low_volatility_block",
                # ENFORCED 2026-05-13 — clean_break-specific drift gate (p90<=5%).
                "filter_clean_break_p90_block",
                # ENFORCED 2026-05-13 — high_regime-specific absorption gate (buyvol<=1.0).
                "filter_high_regime_buyvol_block",
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

    def _load_sticky(self) -> None:
        """Load sticky watchlist from disk. Prunes expired entries on load."""
        try:
            if not os.path.exists(self._sticky_path):
                return
            with open(self._sticky_path) as f:
                raw = json.load(f)
            now = time.time()
            for addr, entry in (raw or {}).items():
                ts = float(entry.get("last_seen_ts", 0))
                if now - ts <= self._sticky_ttl_secs:
                    self._sticky_watchlist[addr] = entry
            logger.info(f"[DipScanner] Loaded {len(self._sticky_watchlist)} sticky-watchlist tokens")
        except Exception as e:
            logger.warning(f"[DipScanner] Could not load sticky_watchlist.json: {e}")

    def _save_sticky(self) -> None:
        """Persist sticky watchlist atomically."""
        try:
            os.makedirs(os.path.dirname(self._sticky_path), exist_ok=True)
            tmp = self._sticky_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._sticky_watchlist, f)
            os.replace(tmp, self._sticky_path)
        except Exception as e:
            logger.warning(f"[DipScanner] Could not save sticky_watchlist.json: {e}")

    def _prune_sticky(self) -> None:
        """Drop sticky-watchlist entries older than TTL."""
        now = time.time()
        before = len(self._sticky_watchlist)
        self._sticky_watchlist = {
            addr: e for addr, e in self._sticky_watchlist.items()
            if now - float(e.get("last_seen_ts", 0)) <= self._sticky_ttl_secs
        }
        if before != len(self._sticky_watchlist):
            logger.info(f"[DipScanner] Sticky-watchlist pruned: {before} -> {len(self._sticky_watchlist)}")

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
                    # 2026-05-14 PM: now polls BOTH Axiom feeds in parallel.
                    # Top tab     → /users-trending-v2 (existing, via fetch_axiom_trending_pairs)
                    # Trending tab → /new-trending-v2  (new, via fetch_axiom_pairs_for_path)
                    # Verified by Playwright CDP capture of logged-in axiom.trade UI.
                    if not axiom_auth:
                        return []
                    try:
                        from feeds.axiom_discovery import (
                            fetch_axiom_trending_pairs,
                            fetch_axiom_pairs_for_path,
                        )
                        async def _trending_tab():
                            try:
                                return await fetch_axiom_pairs_for_path(
                                    axiom_auth, "/new-trending-v2?timePeriod=5m"
                                ) or []
                            except Exception:
                                return []
                        top_pairs, trending_tab_pairs = await asyncio.gather(
                            fetch_axiom_trending_pairs(axiom_auth, time_period="5m"),
                            _trending_tab(),
                            return_exceptions=False,
                        )
                        top_pairs = top_pairs or []
                        trending_tab_pairs = trending_tab_pairs or []
                        # Merge with Top priority — dedupe in caller via pair_by_addr.
                        merged = list(top_pairs) + list(trending_tab_pairs)
                        logger.info(
                            f"[DipScanner] Axiom feeds: top={len(top_pairs)} + "
                            f"trending={len(trending_tab_pairs)} = {len(merged)} pairs"
                        )
                        return merged
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

                # Sticky watchlist re-seed — inject tokens we've seen recently
                # but that aren't on any trending feed THIS cycle. Solves the
                # BURNIE V-bottom universe gap (token drops off trending during
                # cascade, we miss the +6-11% V-recovery entry).
                self._prune_sticky()
                _sticky_added = 0
                for addr, entry in self._sticky_watchlist.items():
                    p = entry.get("pair")
                    if p and addr not in pair_by_addr:
                        pair_by_addr[addr] = p
                        source_by_addr[addr] = "sticky_watchlist"
                        _sticky_added += 1
                if _sticky_added:
                    logger.info(f"[DipScanner] Sticky-watchlist re-seeded {_sticky_added} tokens")

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
                # User watchlist: force-include — always enrich even if not
                # in the universe this cycle, so we can revisit it.
                to_enrich = list(dict.fromkeys(
                    stub_addrs
                    + list(pair_by_addr.keys())
                    + list(self._user_watchlist_addrs)
                ))
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
                            elif addr in self._user_watchlist_addrs:
                                # User watchlist tokens — tag and include even
                                # if not previously in pair_by_addr.
                                pair_by_addr[addr] = p
                                source_by_addr[addr] = "user_watchlist"
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
            "sticky_watchlist": 0,
            "user_watchlist": 0,
        }
        for src in source_by_addr.values():
            source_counts[src] = source_counts.get(src, 0) + 1

        # Persist current universe to sticky watchlist for next-cycle re-seed.
        # Only persist enriched pairs (have full DS pair dict) so re-seeding
        # doesn't inject stale stubs.
        _now = time.time()
        for addr, p in pair_by_addr.items():
            src = source_by_addr.get(addr, "")
            if src in ("ds_stub", "ds_search", "gt_enriched", "axiom_enriched",
                       "sticky_watchlist"):
                self._sticky_watchlist[addr] = {"pair": p, "last_seen_ts": _now}
        self._save_sticky()

        return list(pair_by_addr.values()), source_counts
