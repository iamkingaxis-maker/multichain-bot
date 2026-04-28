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
        # GT trending pools widen the universe beyond DexScreener stubs/searches.
        # Lazy-init so tests can construct without pulling the feeds.gecko deps.
        self.gt_client = gt_client or GeckoTerminalClient(cache_ttl=60, rate_per_min=15)

        self._start_monotonic = time.monotonic()
        self.signals_fired = 0
        self._last_buy_time = 0.0
        self._rejected_distribution = 0
        # h24 history per token for trend-reversal detection.  Each scan cycle
        # appends (wall_ts, pc_h24) for every evaluated token; entries older
        # than 6h are pruned.  Used to reject entries where h24 has collapsed
        # to < 25% of recent peak (the meme is dying — see mexicanunc 04-25).
        # Persisted to /data/h24_history.json so the filter survives deploys.
        self._h24_history: Dict[str, Deque[Tuple[float, float]]] = {}
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

    async def run(self):
        logger.info("[DipScanner] Starting — targeting $1M+ mcap dip entries")
        while True:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"[DipScanner] Scan cycle error: {e}")
            await asyncio.sleep(_SCAN_INTERVAL)

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
            liq_usd = float((pair.get("liquidity") or {}).get("usd") or 0)
            turnover = (vol_h24 / liq_usd) if liq_usd > 0 else 0.0
            if liq_usd > 0 and turnover < self.min_turnover_h24:
                c["low_turnover"] += 1
                continue

            # Volume-decay filter: require recent-hour volume to be at least
            # min_vol_h1_ratio of the 24h average hourly rate, and (optional)
            # non-zero volume in the last 5 minutes. Blocks tokens whose
            # liquidity is fading mid-trade (67: $0 m5 vol; TROLL: 0.48× h1 rate).
            vol_h1 = (pair.get("volume") or {}).get("h1", 0) or 0
            vol_m5 = (pair.get("volume") or {}).get("m5", 0) or 0
            if self.require_vol_m5 and vol_m5 <= 0:
                c["vol_m5_zero"] += 1
                continue
            if vol_h1 < vol_h24 * self.min_vol_h1_ratio / 24:
                c["vol_h1_decay"] += 1
                continue

            pc_h24 = (pair.get("priceChange") or {}).get("h24", 0) or 0
            pc_h6 = (pair.get("priceChange") or {}).get("h6", 0) or 0
            pc_h1 = (pair.get("priceChange") or {}).get("h1", 0) or 0
            pc_m5 = (pair.get("priceChange") or {}).get("m5", 0) or 0

            # Track h24 history for trend-reversal detection — append each cycle
            # and prune entries older than the 6h window.  Wall-clock time so
            # history survives process restarts.
            addr_lower = token_address.lower()
            hist = self._h24_history.setdefault(addr_lower, deque())
            wall_now = time.time()
            hist.append((wall_now, pc_h24))
            while hist and (wall_now - hist[0][0]) > self._h24_history_window_secs:
                hist.popleft()
            self._h24_history_dirty = True

            if pc_h24 <= 0:
                c["red_h24"] += 1
                continue

            # Trend-reversal filter: reject if current h24 has collapsed to
            # <25% of recent peak across last 6h of observations.  Catches
            # tokens whose meme has died — the bot keeps seeing "dips" but
            # they're decay legs, not retracements (e.g. mexicanunc went
            # from h24=+28720% to +823% in 8h, then bled $-300+ as we kept
            # buying every -10% h1 reading).  Backtest: blocks 9 lifetime
            # trades for net +$389 save; only BULL +$48 of winners lost.
            if len(hist) >= self._h24_reversal_min_samples:
                peak_h24 = max(h for _, h in hist)
                # Only fire if the recent peak was a real pump.  Spares
                # established memes (MAGA/BULL/WIFE) doing normal h24 cycling.
                # Also require pc_h6 <= 0 so we only block on actual price
                # decay — guards against the "anchor slide" case where a
                # newly-pumped token (e.g. SCAM: peaked at +39721% h24, now
                # +629%) looks decayed by ratio but is still uptrending on
                # 6h (h6=+57%). True decay = h6 negative.
                if peak_h24 >= self._h24_reversal_min_peak \
                        and (pc_h24 / peak_h24) < self._h24_reversal_threshold \
                        and pc_h6 <= 0:
                    c["trend_reversal"] += 1
                    if len(trend_reversal_blocked) < 6:  # cap log noise
                        trend_reversal_blocked.append(
                            f"{token_symbol}({pc_h24:.0f}%/peak{peak_h24:.0f}%/h6{pc_h6:+.0f}%)"
                        )
                    continue
                # Top-exhaustion filter: token already pumped +50% to +200%
                # over last 6h AND is still pumping (h1 >= +5%).  This is the
                # "small dip on already-extended uptrend" pattern that catches
                # the last burst before reversal — the worst single bucket on
                # 04-27 (-$351 net across 10 trades, lifetime -$334).
                if 50.0 <= peak_h24 <= 200.0 and pc_h1 >= 5.0:
                    c["top_exhaustion"] += 1
                    continue
            if pc_h1 >= 0 and pc_m5 >= 0:
                c["no_dip"] += 1
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
                continue

            # Dip-already-over filter: m5 has turned positive but hasn't built
            # momentum yet ([0%, +3%) band). Historically -EV: n=43, 42% WR,
            # -$50 net. Buying the bounce-top after the dip ended but before
            # the move resumes — top-tick zone. Other m5 buckets are +EV
            # (deep dip, active dip, dip-ending all >50% WR; bouncing/running
            # buckets >75% WR).
            if 0 <= pc_m5 < 3.0:
                c["m5_dip_over"] += 1
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
                continue

            # Mega-pump middle-band filter: on tokens with h24 > +5000%, dip
            # entries cluster into three archetypes by h1:
            #   h1 <= -15%: deep pullback — real bounce setup, wins
            #   h1 >= +50%: raging continuation — wins (ride the pump)
            #   h1 between -15 and +50 with m5<0: "dead middle" — dies out
            # Retro-test: blocks 10 mega-pump trades, catches 6 losses
            # (including today's mexicanunc -$50) for net +$208. Zero
            # impact on MAGA/BULL (h24 never reaches this regime).
            if (pc_h24 > 5000.0
                    and pc_m5 < 0
                    and -15.0 <= pc_h1 <= 50.0):
                c["mega_pump_middle"] += 1
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
                continue
            ratio_h6 = (b_h6 / s_h6) if s_h6 > 0 else float("inf")
            if ratio_h6 < self.min_txn_ratio_h6:
                c["bs_h6"] += 1
                self._rejected_distribution += 1
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
            # blocks 10 trades for net +$287, today saves +$186.  Skip when
            # ratio is 0 or inf (insufficient data — fail open).
            if ratio_h1 != float("inf") and 0 < ratio_h1 < 0.85 and pc_m5 < 0:
                c["seller_h1_red_m5"] += 1
                continue

            # Pumped + sellers cooling filter: token pumped over the hour
            # (h1 > +3%) but the most-recent 5min has m5 sells dominating
            # (bs_m5 < 1) AND price hasn't pulled back meaningfully (m5 > -2%).
            # Pattern: "pumped, now stalling at top with sellers winning the
            # moment" — SPIKE-class top-buy.  Lifetime: blocks 8 trades,
            # saves +$203; today saves +$150.  Fail-open when bs_m5 is 0/inf.
            if (pc_h1 > 3.0 and pc_m5 > -2.0
                    and ratio_m5 != float("inf")
                    and 0 < ratio_m5 < 1.0):
                c["seller_pump"] += 1
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
            m1_features: dict = {}
            if pair_addr_for_1m:
                try:
                    cs = await self.gt_client.fetch_1m(pair_addr_for_1m, limit=5)
                except Exception as _e:
                    logger.debug(f"[DipScanner] 1m fetch error for {token_symbol}: {_e}")
                    cs = []
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
                    m1_features = {
                        "1m_green_in_last3": green_in_last3,
                        "1m_last_close_pct": round(last_close_pct, 3),
                        "1m_cum_3min_pct":   round(cum_3min_pct, 3),
                        "1m_volume_spike":   round(vol_spike_ratio, 3),
                        "1m_candle_count":   len(cs),
                    }
                    if green_in_last3 == 0:
                        c["no_1m_reversal"] += 1
                        logger.info(
                            f"[DipScanner] 1m gate: {token_symbol} — "
                            f"no green close in last 3 min "
                            f"(cum_3min={cum_3min_pct:+.1f}%) — skipping"
                        )
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
                        continue

            # ── Range-position capture (5m candle stack) ──
            # Fetch last 12 × 5m candles (= 1h coverage) to compute where
            # the current price sits in the 1h range.  Stored in entry_meta
            # for future backtesting; not yet used as a filter (gathering
            # data first).  Fail-open on fetch errors.
            range_features: dict = {}
            if pair_addr_for_1m:
                try:
                    cs5 = await self.gt_client.fetch_5m(pair_addr_for_1m, limit=12)
                except Exception as _e:
                    logger.debug(f"[DipScanner] 5m fetch error for {token_symbol}: {_e}")
                    cs5 = []
                if cs5 and len(cs5) >= 4:
                    high_1h = max(k.high for k in cs5)
                    low_1h = min(k.low for k in cs5)
                    cur_price = cs5[-1].close
                    rng_1h = high_1h - low_1h
                    pct_in_1h_range = (
                        (cur_price - low_1h) / rng_1h if rng_1h > 0 else 0.5
                    )
                    range_features = {
                        "1h_high": round(high_1h, 8),
                        "1h_low": round(low_1h, 8),
                        "5m_candle_count": len(cs5),
                        "pct_in_1h_range": round(pct_in_1h_range, 3),
                    }

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

            # Batch 1 entry-meta — anything dip_scanner has at this moment that's
            # nice-to-have for analysis but doesn't merit its own Position field.
            pair_age_hours = (now_ms - created_ms) / 3_600_000 if created_ms > 0 else 0.0
            peak_h24_6h = max((h for _, h in hist), default=pc_h24)
            cycles_seen = len(hist)
            txns_h1_total = b_h1 + s_h1
            avg_trade_size_h1 = (vol_h1 / txns_h1_total) if txns_h1_total > 0 else 0.0
            entry_meta_dict = {
                "liquidity_usd": float(liq_usd or 0),
                "protocol": pair.get("dexId", "") or "",
                "peak_h24_6h_pct": float(peak_h24_6h),
                "h24_ratio_to_peak": (pc_h24 / peak_h24_6h) if peak_h24_6h > 0 else 1.0,
                "cycles_seen_before_buy": cycles_seen,
                "avg_trade_size_h1_usd": avg_trade_size_h1,
                "bs_h6": float(ratio_h6) if ratio_h6 != float("inf") else None,
                "bs_h1": float(ratio_h1) if ratio_h1 != float("inf") else None,
                "bs_m5": float(ratio_m5) if ratio_m5 != float("inf") else None,
                **m1_features,  # 1m candle features (or empty if fetch failed)
                **range_features,  # 5m range features (or empty if fetch failed)
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
        """Load persisted h24 history; drop entries older than the 6h window."""
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
                fresh = [(float(ts), float(h)) for ts, h in entries
                         if isinstance(ts, (int, float)) and float(ts) > cutoff]
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
        """Write h24 history atomically (tmp + rename).  Lists, not deques."""
        try:
            os.makedirs(os.path.dirname(self._h24_history_path), exist_ok=True)
            tmp_path = self._h24_history_path + ".tmp"
            payload: Dict[str, List[List[float]]] = {
                addr: [[ts, h] for ts, h in dq]
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
