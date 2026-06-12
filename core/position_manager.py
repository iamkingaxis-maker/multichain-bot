"""
Position Manager
Encodes the trader's exact rules for managing open positions.
"""

import asyncio
import json
import logging
import os
import aiohttp
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── smart_follow stop-grace A/B (2026-06-10) ────────────────────────────────
# See PositionState.stop_grace for the evidence. Paper A/B: treatment assigned
# by deterministic token-address parity so the arm is recomputable offline from
# the trade record (no new persistence needed).
def _grace_mode() -> str:
    m = os.environ.get("SMART_FOLLOW_STOP_GRACE_MODE", "ab").strip().lower()
    return m if m in ("ab", "all", "off") else "ab"


def _grace_minutes() -> float:
    try:
        return float(os.environ.get("SMART_FOLLOW_STOP_GRACE_MIN", "45"))
    except Exception:
        return 45.0


def _grace_floor_pct() -> float:
    """Catastrophic stop that still fires during grace (rug guard)."""
    try:
        return float(os.environ.get("SMART_FOLLOW_STOP_GRACE_FLOOR_PCT", "50"))
    except Exception:
        return 50.0


def _follow_tp1_fraction() -> float:
    try:
        return float(os.environ.get("SMART_FOLLOW_TP1_FRACTION", "0.35"))
    except Exception:
        return 0.35


def _stop_grace_arm(token_address: str) -> bool:
    """Deterministic ~50/50 treatment assignment by token-address parity."""
    mode = _grace_mode()
    if mode == "off":
        return False
    if mode == "all":
        return True
    return sum(ord(c) for c in (token_address or "")) % 2 == 0

COINGECKO_BTC = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
DEXSCREENER_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/"


@dataclass
class VolumeWindow:
    """30-minute volume observation."""
    volume_usd: float
    timestamp: datetime
    buy_count: int = 0
    sell_count: int = 0


@dataclass
class PositionState:
    """Full state for one open position."""
    token_address: str
    token_symbol: str
    chain_id: str
    entry_price: float
    entry_volume_usd: float    # h1 volume snapshot at entry — the baseline
    position_size_usd: float
    original_size_usd: float   # Before any average down
    entry_time: datetime

    # Buy reason — "micro" substring → is_micro_cap
    reason: str = ""
    is_micro_cap: bool = False
    strategy: str = "scanner"  # "graduation" gets wider stop loss
    # smart_follow stop-grace A/B (2026-06-10, AxiS-approved): post-stop trajectory
    # test on all 19 hard-stopped smart_follow positions showed 14 (74%) recovered
    # >15% above the stop within 12h (median ~+35%) — the stops fire into the entry
    # whipsaw of multi-hour theses. Treatment arm (deterministic token-address
    # parity) suppresses the hard stop for the first SMART_FOLLOW_STOP_GRACE_MIN
    # minutes, with a catastrophic floor (SMART_FOLLOW_STOP_GRACE_FLOOR_PCT) that
    # always stops — bounds the 4-in-19 keep-falling case. Control = current stops.
    follow_origin: bool = False
    stop_grace: bool = False  # treatment arm: hard stop deferred during grace window
    # Per-position TP1 sell-fraction override (2026-06-08): smart_follow rides fast-fade
    # momentum spikes that peak +8-17% then give back ~15pp on the dip ladder's 50%-then-
    # trail. Setting this higher (take more at TP1) captures the pop. None = use dip_tp1_sell.
    tp1_sell_override: Optional[float] = None

    # TP tracking
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False

    # Realtime exhaustion trail — set when soft-trail trigger first fires
    # (price drops >=1.5pp from peak with peak >= +3%). Cleared on recovery.
    # When the position has been pending for the confirmation window, exit
    # fires. See check_exhaustion_realtime.
    pending_exit_since_ts: Optional[float] = None

    # Realtime post-TP1 trail — set when remainder is dropping from its
    # post-TP1 peak. Separate from pending_exit_since_ts (pre-TP1 only) so
    # both phases can use the confirm-window pattern independently. See
    # check_post_tp1_trail_realtime.
    post_tp1_pending_ts: Optional[float] = None

    # Breakeven tracking
    breakeven_locked: bool = False

    # Stall tracking
    volume_windows: List[VolumeWindow] = field(default_factory=list)
    stall_exit_done: bool = False
    current_m5_volume: float = 0.0
    current_h1_volume: float = 0.0

    # Average down tracking
    averaged_down: bool = False
    avg_down_price: float = 0.0

    # Pyramid tracking
    pyramided: bool = False
    pyramid_signal_score: int = 0
    hh_hl_confirmed: bool = False

    # Liquidity tracking
    current_liquidity_usd: float = 0.0
    liquidity_confirmed: bool = True
    dead_liquidity_since: Optional[datetime] = None

    # Transaction rate tracking (h1 buys+sells from DexScreener REST)
    entry_txns_h1: int = 0    # baseline set on first REST update
    current_txns_h1: int = 0  # refreshed every 5s REST poll

    # Current state
    current_price: float = 0.0
    current_volume_usd: float = 0.0
    peak_price: float = 0.0
    min_price_usd: float = 0.0

    # Scalp 4-phase detector metadata: sweep_low, stop_price, tp1_price, entry_close_time, pool_address
    scalp_meta: Optional[dict] = None

    # Pair (pool) address — required for mid-hold chart_reader signal-flip
    # exits. dip_buy entries set this from pair_addr_for_1m. Empty for legacy
    # positions opened before the field existed.
    pair_address: str = ""

    # Mid-hold chart-reader signal-flip exit state (shadow mode: logged
    # but not enforced — pending forward-data validation against the
    # already-dropped trail experiment from 2026-05-01).
    last_chart_check_mono: float = 0.0
    consecutive_bearish_flips: int = 0
    # First wall-clock timestamp at which signal-flip detector first flipped
    # bearish, plus the pnl_pct at that moment. Persisted into entry_meta on
    # sell so we can analyze "would early exit have helped?" in 10pm reports.
    signal_flip_first_ts: Optional[datetime] = None
    signal_flip_first_pnl: Optional[float] = None
    signal_flip_reasons: List[str] = field(default_factory=list)

    # Chart-feature cache for the position_tick logger — populated by
    # _maybe_check_chart_signal_flip every 60s. The 5s tick logger reads
    # this and stamps each tick with the most-recent chart features so
    # the smart-TP miner has bs/mtf/structure context alongside pnl_pct.
    chart_features_cache: Optional[dict] = None
    chart_features_cache_ts: float = 0.0

    # Smart-TP peak-detector state (SHADOW 2026-05-14 PM).
    # Logs first time the composed peak_score crossed threshold while in
    # green. Mirror of signal_flip_* fields. Used to validate "would-have-
    # sold-earlier" forward before promoting to ENFORCED exit.
    peak_detect_first_ts: Optional[datetime] = None
    peak_detect_first_pnl: Optional[float] = None
    peak_detect_first_score: Optional[int] = None
    peak_detect_first_reasons: List[str] = field(default_factory=list)
    # Previous-tick price for velocity comparison.
    _prev_tick_price: float = 0.0
    _prev_tick_ts: float = 0.0

    # 1s cascade detector — SHADOW 2026-05-11. Checks during hold whether
    # the 1s structure shows an active cascade (volatile, mostly-red,
    # close-near-low). If yes, logs the wall-clock time + pnl at first
    # detection. Persisted into entry_meta on sell for "would 1s-cascade
    # exit have saved $" analysis.
    last_1s_cascade_check_mono: float = 0.0
    cascade_1s_first_ts: Optional[datetime] = None
    cascade_1s_first_pnl: Optional[float] = None
    cascade_1s_consec: int = 0
    cascade_1s_reasons: List[str] = field(default_factory=list)

    # Fast-dud exit shadow — SHADOW 2026-05-11. Tighter stop applied to
    # positions open >=60s that never crossed +1.0% peak AND are at -1.5%
    # or worse. Records first-fire time + pnl. Persisted into entry_meta
    # on sell for "would fast-dud have saved $" analysis. Past-7d sim
    # shows 83 fires / 0 harmed / +$164. Held-out: 3 fires / 0 harmed.
    fast_dud_first_ts: Optional[datetime] = None
    fast_dud_first_pnl: Optional[float] = None

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return ((self.current_price - self.entry_price) / self.entry_price) * 100

    @property
    def hours_open(self) -> float:
        return (datetime.now(timezone.utc) - self.entry_time).total_seconds() / 3600

    @property
    def stall_threshold(self) -> float:
        if self.entry_volume_usd >= 100_000:
            return 0.15
        elif self.entry_volume_usd >= 20_000:
            return 0.20
        else:
            return 0.30

    @property
    def is_stalled(self) -> bool:
        if self.entry_volume_usd <= 0:
            return False
        if not self.volume_windows:
            return False
        threshold = self.entry_volume_usd * self.stall_threshold
        m5_hourly_rate = self.current_m5_volume * 12
        m5_stalled = m5_hourly_rate < threshold
        h1_stalled = self.current_h1_volume < threshold
        price_not_rising = self.current_price <= self.peak_price * 0.99
        stalled = m5_stalled and h1_stalled and price_not_rising
        if stalled:
            logger.debug(
                f"[Stall] {self.token_symbol} | "
                f"m5×12: ${m5_hourly_rate:,.0f} | "
                f"h1: ${self.current_h1_volume:,.0f} | "
                f"Threshold: ${threshold:,.0f} "
                f"({self.stall_threshold*100:.0f}% of ${self.entry_volume_usd:,.0f}) | "
                f"Price {self.current_price:.6f} vs peak {self.peak_price:.6f}"
            )
        return stalled

    @property
    def volume_declining(self) -> bool:
        return self.is_stalled

    def add_volume_window(self, volume: float, buys: int = 0, sells: int = 0):
        self.volume_windows.append(VolumeWindow(
            volume_usd=volume,
            timestamp=datetime.now(timezone.utc),
            buy_count=buys,
            sell_count=sells
        ))
        if len(self.volume_windows) > 8:
            self.volume_windows = self.volume_windows[-8:]


class MarketConditionMonitor:
    def __init__(self,
                 btc_drop_threshold: float = 5.0,
                 restricted_score_threshold: int = 85,
                 override_score: int = 90,
                 normal_score_threshold: int = 65):
        self.btc_drop_threshold = btc_drop_threshold
        self.restricted_threshold = restricted_score_threshold
        self.override_score = override_score
        self.normal_threshold = normal_score_threshold

        self.btc_change_24h: float = 0.0
        self.market_restricted: bool = False
        self.restriction_reason: str = ""
        self.last_checked: Optional[datetime] = None

        self._on_restrict_callbacks: List[Callable] = []
        self._on_resume_callbacks: List[Callable] = []

    def on_restrict(self, cb: Callable):
        self._on_restrict_callbacks.append(cb)

    def on_resume(self, cb: Callable):
        self._on_resume_callbacks.append(cb)

    async def run(self):
        logger.info("[MarketMonitor] Started — watching BTC 24h change")
        while True:
            try:
                await self._check_conditions()
            except Exception as e:
                logger.debug(f"[MarketMonitor] Error: {e}")
            await asyncio.sleep(900)  # 15 minutes

    async def _check_conditions(self):
        change = await self._fetch_btc_change()
        if change is None:
            return
        self.btc_change_24h = change
        self.last_checked = datetime.now(timezone.utc)
        was_restricted = self.market_restricted
        if change <= -self.btc_drop_threshold:
            self.market_restricted = True
            self.restriction_reason = f"BTC {change:.1f}% 24h"
            if not was_restricted:
                for cb in self._on_restrict_callbacks:
                    try:
                        cb(self.restriction_reason)
                    except Exception:
                        pass
        else:
            self.market_restricted = False
            self.restriction_reason = ""
            if was_restricted:
                for cb in self._on_resume_callbacks:
                    try:
                        cb()
                    except Exception:
                        pass

    async def _fetch_btc_change(self) -> Optional[float]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    COINGECKO_BTC, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return float(data.get("bitcoin", {}).get("usd_24h_change", 0) or 0)
        except Exception:
            return None

    def get_current_threshold(self, signal_score: int = 0) -> int:
        if signal_score >= self.override_score:
            return self.override_score
        if self.market_restricted:
            return self.restricted_threshold
        return self.normal_threshold

    def should_trade(self, signal_score: int) -> bool:
        return signal_score >= self.get_current_threshold(signal_score)

    def get_stats(self) -> dict:
        return {
            "btc_24h_change": round(self.btc_change_24h, 2),
            "market_restricted": self.market_restricted,
            "restriction_reason": self.restriction_reason,
            "current_threshold": self.get_current_threshold(),
            "last_checked": self.last_checked.isoformat() if self.last_checked else None
        }


class PositionManager:
    """
    Manages all open positions with the trader's exact rules.
    Runs as a background task alongside the main trader.
    """

    def __init__(self,
                 chain_name: str,
                 chain_id: str,
                 trader,
                 open_positions_ref: dict,
                 telegram,
                 tracker,
                 market_monitor: MarketConditionMonitor,

                 # Standard take profit tiers
                 tp1_pct: float = 10.0,
                 tp1_sell: float = 1.0,
                 tp2_pct: float = 75.0,
                 tp2_sell: float = 0.40,
                 tp3_pct: float = 150.0,
                 tp3_sell: float = 1.0,

                 # Standard stop loss
                 stop_loss_pct: float = 7.0,

                 # Winner protection
                 winner_trail_pct: float = 15.0,

                 # Stall detection
                 stall_check_interval_min: int = 5,
                 stall_volume_threshold: float = 0.20,
                 stall_min_hours: float = 0.1,
                 stall_sell_pct: float = 1.0,

                 # Average down
                 avg_down_max_loss_pct: float = 4.0,
                 avg_down_min_volume_pct: float = 0.50,
                 avg_down_size_pct: float = 0.50,

                 # Micro-cap specific TP/SL
                 mc_tp1_pct: float = 10.0,
                 mc_tp1_sell: float = 1.0,
                 mc_tp2_pct: float = 75.0,
                 mc_tp2_sell: float = 0.40,
                 mc_tp3_pct: float = 200.0,
                 mc_tp3_sell: float = 1.0,
                 mc_stop_loss_pct: float = 25.0,
                 mc_winner_trail_pct: float = 15.0,

                 # Dip buy specific TP/SL — defaults match utils/config.py
                 # so test/script instantiations without explicit kwargs see
                 # production behavior. main.py still passes config.* values
                 # explicitly at startup.
                 dip_tp1_pct: float = 3.0,
                 dip_tp1_sell: float = 0.50,
                 dip_tp2_pct: float = 5.0,
                 dip_tp2_sell: float = 0.50,
                 dip_tp3_pct: float = 10.0,
                 dip_tp3_sell: float = 1.0,
                 dip_stop_pct: float = 15.0,
                 dip_winner_trail_pct: float = 3.5,

                 # Scalp strategy TP/SL (4-phase rewrite)
                 scalp_tp1_pct: float = 10.0,
                 scalp_tp1_sell: float = 0.50,
                 scalp_tp2_pct: float = 15.0,
                 scalp_tp2_sell: float = 0.35,
                 scalp_stop_pct: float = 6.0,
                 scalp_time_exit_candles: int = 4,
                 scalp_time_exit_min_pct: float = 5.0,
                 scalp_max_hold_minutes: float = 45.0,

                 # Scalper reference — for breakeven-after-scalp check
                 scalper=None,
                 # Scanner reference — for stop-loss cooldown registration
                 scanner=None,
                 # GeckoTerminal + DexScreener clients for mid-hold chart re-eval.
                 # If both None, signal-flip exit is disabled (legacy behaviour).
                 gt_client=None,
                 dexs_client=None):

        self.chain_name = chain_name
        self.chain_id = chain_id
        self.trader = trader
        self.open_positions_ref = open_positions_ref
        self.telegram = telegram
        self.tracker = tracker
        self.market_monitor = market_monitor
        self.scanner = scanner
        self.gt_client = gt_client
        self.dexs_client = dexs_client

        # Standard TP settings
        self.tp1_pct = tp1_pct
        self.tp1_sell = tp1_sell
        self.tp2_pct = tp2_pct
        self.tp2_sell = tp2_sell
        self.tp3_pct = tp3_pct
        self.tp3_sell = tp3_sell

        # Standard SL
        self.stop_loss_pct = stop_loss_pct
        self.winner_trail_pct = winner_trail_pct

        # MC settings
        self.mc_tp1_pct = mc_tp1_pct
        self.mc_tp1_sell = mc_tp1_sell
        self.mc_tp2_pct = mc_tp2_pct
        self.mc_tp2_sell = mc_tp2_sell
        self.mc_tp3_pct = mc_tp3_pct
        self.mc_tp3_sell = mc_tp3_sell
        self.mc_stop_loss_pct = mc_stop_loss_pct
        self.mc_winner_trail_pct = mc_winner_trail_pct

        # Dip buy settings — 3-tier ladder
        self.dip_tp1_pct = dip_tp1_pct
        self.dip_tp1_sell = dip_tp1_sell
        self.dip_tp2_pct = dip_tp2_pct
        self.dip_tp2_sell = dip_tp2_sell
        self.dip_tp3_pct = dip_tp3_pct
        self.dip_tp3_sell = dip_tp3_sell
        self.dip_stop_pct = dip_stop_pct
        self.dip_winner_trail_pct = dip_winner_trail_pct

        # Scalp (4-phase rewrite)
        self.scalp_tp1_pct = scalp_tp1_pct
        self.scalp_tp1_sell = scalp_tp1_sell
        self.scalp_tp2_pct = scalp_tp2_pct
        self.scalp_tp2_sell = scalp_tp2_sell
        self.scalp_stop_pct = scalp_stop_pct
        self.scalp_time_exit_candles = scalp_time_exit_candles
        self.scalp_time_exit_min_pct = scalp_time_exit_min_pct
        self.scalp_max_hold_minutes = scalp_max_hold_minutes
        self.scalp_queue = None  # set by main.py after construction

        # Stall
        self.stall_interval = stall_check_interval_min
        self.stall_volume_threshold = stall_volume_threshold
        self.stall_min_hours = stall_min_hours
        self.stall_sell_pct = stall_sell_pct

        # Average down
        self.avg_down_max_loss = avg_down_max_loss_pct
        self.avg_down_min_volume = avg_down_min_volume_pct
        self.avg_down_size = avg_down_size_pct

        self.scalper = scalper

        self._states: Dict[str, PositionState] = {}
        self._last_volume_check: Dict[str, datetime] = {}
        self._stop_triggered: set = set()  # De-dup for realtime stops
        self._grace_logged: set = set()    # one STOP GRACE log per token (realtime path)
        self._tp_triggered: set = set()    # De-dup for realtime TPs
        self._trail_triggered: set = set() # De-dup for realtime pre-TP1 trail
        self._post_tp1_trail_triggered: set = set()  # De-dup for realtime post-TP1 trail
        # Last realtime price per token — used as the reference for the
        # downside sanity gate (reject ticks that drop >20% from prior).
        # Catches single corrupted feed ticks that fire spurious -15% stops
        # then recover (lifetime: ~55% of stops realized at <-14.5%).
        self._last_realtime_price: dict = {}
        # Pending price-spike confirmations for the anti-corruption guards.
        # Maps token_address -> {'price', 'count', 'first_seen'}.
        # When a tick exceeds ±20% of ref_price the guard rejects it as
        # "corrupted" — but real memecoin pumps move 50-100%+ in seconds and
        # every WS tick at the new price was being rejected against the
        # stale pre-pump ref_price. HANTA-Kun 2026-05-08 legitimate +106%
        # move stayed permanently rejected (state stuck at $0.000128 while
        # market traded at $0.000264) — no TPs/stops fired. Confirmation:
        # after 3 same-price ticks (within 5%, 60s window) OR 30s of
        # sustained rejection, accept the move. Goblin -94% glitch (single
        # one-off tick) won't accumulate, so it still gets rejected.
        self._spike_pending: Dict[str, dict] = {}
        self._last_rest_fetch: Dict[str, float] = {}  # token → unix ts of last REST call
        self.axiom_price_feed = None  # Set by main.py — socket8 real-time cache (~ms latency)
        self.rpc_price_feed   = None  # Set by main.py — Solana RPC + Jupiter 0.5s poll
        self.dex_price_feed = None    # Set by main.py — DexScreener 1s poll cache (fallback)

        # Stats
        self.tp1_hits = 0
        self.tp2_hits = 0
        self.tp3_hits = 0
        self.stop_loss_hits = 0
        self.stall_exits = 0
        self.avg_downs = 0

        # Position-tick logger — write a JSONL line per evaluated tick to
        # mine adaptive-TP signals later. Drives the "smart TP" project:
        # collect (pnl_pct, peak_pct, giveback_pct, age, velocity, vol) over
        # the full hold of each position, then mine which features at peak
        # predict how much of the gain gets given back. Output:
        #   {DATA_DIR}/position_ticks.jsonl  (append-only)
        # Line schema (one per tick per position):
        #   {addr, sym, ts, age_s, entry_px, cur_px, pnl_pct, peak_pct,
        #    giveback_pct, vol_h1, dvol_pct, liq_usd, dliq_pct, strategy}
        # Fail-safe: any write error is swallowed (read-only intent — never
        # affect exit decisions).
        self._position_tick_path = os.path.join(
            os.environ.get("DATA_DIR", "/data"), "position_ticks.jsonl"
        )
        # Per-token last-tick price/vol/liq for velocity computation.
        self._pt_prev: Dict[str, dict] = {}

    # ────────────────────────────────────────────────────────────────
    # SMART-TP PEAK DETECTOR — SHADOW 2026-05-14 PM
    # ────────────────────────────────────────────────────────────────
    # Goal: identify the local top FORMING and exit BEFORE giveback,
    # instead of waiting for the trail to fire after we've already lost
    # half the gain. Composes 5-7 orthogonal topping signals into a 0-100
    # score; logs WOULD-SELL when score >= threshold AND in green.
    #
    # Validated path: SHADOW for 24h → mine logs for "would peak_detect
    # have exited at higher pnl% than actual trail?" → promote to ENFORCED.
    #
    # Mining basis: .audit_trades.json showed 1-3% peak bucket has 6.89x
    # giveback ratio (peaks +1.77% → -9.62% final, n=6) — the bot rides
    # small peaks all the way to stops. Score components below are derived
    # from chart_reader topping verdicts that exist but aren't used for
    # exits today.

    _PEAK_DETECT_SCORE_THRESHOLD: int = 50
    _PEAK_DETECT_MIN_PNL_PCT: float = 1.0  # only fire when in green
    _PEAK_DETECT_CHART_MAX_AGE_S: float = 90.0  # don't fire on stale features

    def _compute_peak_score(self, state: PositionState, pnl_pct: float,
                             dpx_pct_per_s: float) -> tuple:
        """Returns (score, list[reasons]).
        Score components mirror the topping signals chart_reader already
        computes during signal-flip checks. Cap at 100; threshold 50.
        Asymmetric — only fires when pnl_pct >= _PEAK_DETECT_MIN_PNL_PCT.
        """
        if pnl_pct < self._PEAK_DETECT_MIN_PNL_PCT:
            return 0, []
        cf = state.chart_features_cache or {}
        cf_ts = state.chart_features_cache_ts or 0.0
        if not cf:
            return 0, []
        if (time.time() - cf_ts) > self._PEAK_DETECT_CHART_MAX_AGE_S:
            return 0, []  # stale — skip rather than fire on old data
        score = 0
        reasons: list = []
        # Pattern signal — bearish engulfing / shooting star at peak
        pat_dir = cf.get("pattern_5m_dir")
        pat_conf = float(cf.get("pattern_5m_conf") or 0.0)
        if pat_dir == "bearish" and pat_conf >= 0.5:
            score += 25
            reasons.append(f"pattern_5m=bearish (conf={pat_conf:.2f})")
        # Sweep verdict — high taken then rejected
        sweep_v = cf.get("sweep_5m_verdict")
        if sweep_v == "BEARISH_SWEEP":
            score += 20
            reasons.append("sweep_5m=BEARISH_SWEEP")
        # Trendline breakdown
        tl_v = cf.get("trendline_5m_verdict")
        if tl_v == "BREAKDOWN":
            score += 15
            reasons.append("trendline_5m=BREAKDOWN")
        # Structure state flipped to downtrend
        struct_state = cf.get("struct_5m_state") or ""
        if isinstance(struct_state, str) and struct_state.lower() in (
            "downtrend", "reversal_down"
        ):
            score += 15
            reasons.append(f"struct_5m={struct_state}")
        # Most recent 5m candle red
        if cf.get("last_5m_dir") == "red":
            score += 10
            reasons.append("last_5m=red")
        # Composite score deteriorating (entry was likely >= 50, now sub-40)
        comp = cf.get("composite_score")
        if comp is not None and float(comp) < 40.0:
            score += 10
            reasons.append(f"chart_score={float(comp):.0f}<40")
        # Real-time velocity — price falling now (>0.05%/sec = >18%/hr)
        if dpx_pct_per_s is not None and dpx_pct_per_s < -0.05:
            score += 10
            reasons.append(f"velocity={dpx_pct_per_s:+.3f}%/s falling")
        return min(100, score), reasons

    def _maybe_peak_detect_shadow(self, state: PositionState,
                                    pnl_pct: float) -> None:
        """SHADOW mode — log WOULD-SELL when peak score crosses threshold.
        Does NOT actually trigger a sell yet. Logged for forward validation.
        """
        # Compute price velocity from prior tick
        dpx = 0.0
        if state._prev_tick_ts and state._prev_tick_price > 0:
            dt_s = max(0.001, time.time() - state._prev_tick_ts)
            dpx = (state.current_price - state._prev_tick_price) / state._prev_tick_price * 100.0 / dt_s
        state._prev_tick_price = state.current_price
        state._prev_tick_ts = time.time()

        score, reasons = self._compute_peak_score(state, pnl_pct, dpx)
        if score >= self._PEAK_DETECT_SCORE_THRESHOLD:
            if state.peak_detect_first_ts is None:
                state.peak_detect_first_ts = datetime.now(timezone.utc)
                state.peak_detect_first_pnl = pnl_pct
                state.peak_detect_first_score = score
                state.peak_detect_first_reasons = reasons
                _peak_pnl_so_far = 0.0
                try:
                    _tp_obj = self.open_positions_ref.get(state.token_address)
                    _peak_pnl_so_far = float(getattr(_tp_obj, "peak_pnl_pct", 0.0) or 0.0)
                except Exception:
                    pass
                logger.info(
                    f"[PositionManager/{self.chain_name}] SHADOW PEAK-DETECT WOULD-SELL "
                    f"{state.token_symbol} score={score} pnl={pnl_pct:+.2f}% "
                    f"peak={_peak_pnl_so_far:+.2f}% reasons=[{', '.join(reasons)}]"
                )

    def _log_position_tick(self, token_address: str, state: PositionState,
                            tp_obj=None) -> None:
        """Append one JSONL line per evaluated position tick. Fail-safe."""
        try:
            if state.current_price <= 0 or state.entry_price <= 0:
                return
            pnl_pct = (state.current_price / state.entry_price - 1) * 100.0
            peak_pct = getattr(tp_obj, "peak_pnl_pct", 0.0) or 0.0
            giveback = peak_pct - pnl_pct if peak_pct > 0 else 0.0
            age_s = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
            prev = self._pt_prev.get(token_address, {})
            # Price velocity: %/sec since prior tick (signed)
            dpx_pct_per_s = 0.0
            if prev.get("ts") and prev.get("px"):
                dt_s = max(0.001, time.time() - prev["ts"])
                dpx_pct_per_s = (state.current_price - prev["px"]) / prev["px"] * 100.0 / dt_s
            # Volume velocity: 1h-volume delta vs prior tick
            dvol_pct = 0.0
            if prev.get("vol") and state.current_h1_volume:
                dvol_pct = (state.current_h1_volume - prev["vol"]) / max(1.0, prev["vol"]) * 100.0
            # Liquidity velocity
            dliq_pct = 0.0
            if prev.get("liq") and state.current_liquidity_usd:
                dliq_pct = (state.current_liquidity_usd - prev["liq"]) / max(1.0, prev["liq"]) * 100.0
            tick = {
                "addr": token_address,
                "sym": state.token_symbol,
                "ts": time.time(),
                "age_s": round(age_s, 1),
                "entry_px": state.entry_price,
                "cur_px": state.current_price,
                "pnl_pct": round(pnl_pct, 3),
                "peak_pct": round(peak_pct, 3),
                "giveback_pct": round(giveback, 3),
                "dpx_pct_per_s": round(dpx_pct_per_s, 4),
                "vol_h1": state.current_h1_volume,
                "dvol_pct": round(dvol_pct, 2),
                "liq_usd": state.current_liquidity_usd,
                "dliq_pct": round(dliq_pct, 2),
                "strategy": getattr(state, "strategy", ""),
                "tp1_hit": bool(getattr(tp_obj, "tp1_hit", False)) if tp_obj else False,
                "tp2_hit": bool(getattr(tp_obj, "tp2_hit", False)) if tp_obj else False,
            }
            # Chart features (populated every 60s by signal-flip check);
            # stamped on every tick with chart_age_s so the miner can
            # detect stale features and weight accordingly.
            cf = getattr(state, "chart_features_cache", None)
            cf_ts = getattr(state, "chart_features_cache_ts", 0.0) or 0.0
            if cf:
                tick["chart_age_s"] = round(time.time() - cf_ts, 1)
                for k, v in cf.items():
                    tick[f"chart_{k}"] = v
            os.makedirs(os.path.dirname(self._position_tick_path), exist_ok=True)
            # Disk rotation: cap at 50MB. If over, trim to last 40MB by
            # discarding oldest lines. Cheap O(N) on the rare cycle that
            # crosses the threshold; no-op otherwise.
            try:
                if os.path.getsize(self._position_tick_path) > 50 * 1024 * 1024:
                    with open(self._position_tick_path, "r") as f:
                        f.seek(-40 * 1024 * 1024, os.SEEK_END)
                    # Walk forward to next newline so we don't keep a partial line
                    with open(self._position_tick_path, "rb") as f:
                        f.seek(-40 * 1024 * 1024, os.SEEK_END)
                        f.readline()  # discard partial first line
                        kept = f.read()
                    with open(self._position_tick_path, "wb") as f:
                        f.write(kept)
            except (OSError, ValueError):
                pass  # file shorter than rotation threshold; no-op
            with open(self._position_tick_path, "a") as f:
                f.write(json.dumps(tick) + "\n")
            # Update prev-tick state for next velocity calc
            self._pt_prev[token_address] = {
                "ts": time.time(),
                "px": state.current_price,
                "vol": state.current_h1_volume,
                "liq": state.current_liquidity_usd,
            }
        except Exception:
            pass  # Fail-safe — never affect exit decisions

    async def run(self):
        """Main position management loop — checks every 5 seconds (price from Axiom cache, REST throttled to 30s)."""
        logger.info(
            f"[PositionManager/{self.chain_name}] Started\n"
            f"  DIP TP1: +{self.dip_tp1_pct}% → sell {self.dip_tp1_sell*100:.0f}%\n"
            f"  DIP TP2: +{self.dip_tp2_pct}% → sell {self.dip_tp2_sell*100:.0f}% remaining\n"
            f"  DIP Stop: -{self.dip_stop_pct}% hard | trail after TP1: {self.dip_winner_trail_pct}% from peak\n"
            f"  SCALP TP1: +{self.scalp_tp1_pct}% sell {self.scalp_tp1_sell*100:.0f}% | TP2: +{self.scalp_tp2_pct}% sell {self.scalp_tp2_sell*100:.0f}%\n"
            f"  SCALP Stop: -{self.scalp_stop_pct}% | max-hold: {self.scalp_max_hold_minutes}min\n"
            f"  MC TP1: +{self.mc_tp1_pct}% → sell {self.mc_tp1_sell*100:.0f}%\n"
            f"  MC Stop: -{self.mc_stop_loss_pct}%\n"
            f"  Standard (legacy fallback) TP1/TP2/TP3: +{self.tp1_pct}/+{self.tp2_pct}/+{self.tp3_pct}% — applies to non-dip/non-scalp/non-MC only"
        )
        while True:
            try:
                await self._management_cycle()
            except Exception as e:
                logger.error(f"[PositionManager/{self.chain_name}] Error: {e}")
            await asyncio.sleep(5)

    async def _management_cycle(self):
        """One full management cycle across all open positions."""
        open_addrs = set(self.open_positions_ref.keys())

        # Remove closed positions
        for addr in list(self._states.keys()):
            if addr not in open_addrs:
                # Peak recorder finalize — dump trace before deleting state
                try:
                    from core.peak_recorder import get_recorder
                    closed_state = self._states[addr]
                    get_recorder().finalize(
                        addr,
                        exit_reason='cycle_close_detected',
                        exit_pnl=0.0,
                    )
                except Exception:
                    pass
                del self._states[addr]
                self._stop_triggered.discard(addr)
                self._last_realtime_price.pop(addr, None)

        # Initialize new positions
        for addr in open_addrs:
            if addr not in self._states:
                pos = self.open_positions_ref[addr]
                _reason = getattr(pos, "reason", "")
                _is_mc = "micro" in _reason.lower()
                entry_px = getattr(pos, "entry_price_usd", 0)
                # Restore TP flags from the persisted Position. Without this,
                # a redeploy mid-position resets tp1_hit→False and re-fires
                # Dip TP1 every cycle, halving the position repeatedly.
                self._states[addr] = PositionState(
                    token_address=addr,
                    token_symbol=getattr(pos, "token_symbol", "?"),
                    chain_id=self.chain_id,
                    entry_price=entry_px,
                    entry_volume_usd=0.0,
                    position_size_usd=getattr(pos, "amount_usd", 0),
                    original_size_usd=getattr(pos, "amount_usd", 0),
                    entry_time=getattr(pos, "entry_time", datetime.now(timezone.utc)),
                    reason=_reason,
                    is_micro_cap=_is_mc,
                    # smart_follow inherits the tuned DIP exit ladder (TP1 +3% / runner-
                    # tilt / trail / stop) instead of the loose +35% standard fallback
                    # (2026-06-08): it follows elites into a spike that pops +10-20% then
                    # fades — the +35% standard ladder never fired, so it took ZERO profit
                    # and round-tripped every winner. This maps ONLY the internal exit
                    # state; the trade record (trader.sell -> pos.strategy) stays tagged
                    # 'smart_follow' for the dashboard + analysis.
                    # startswith: covers smart_follow + the K-tier pods
                    # (smart_follow_k2 / smart_follow_solo, 2026-06-10) so every
                    # follow flavor inherits the tuned dip ladder + overrides.
                    strategy=("dip_buy"
                              if getattr(pos, "strategy", "").startswith("smart_follow")
                              else getattr(pos, "strategy", "scanner")),
                    # smart_follow TP1 fraction. 0.85 (bank-the-pop, fixes fader giveback)
                    # CAPPED runners — POKE peaked +321% but 85% was dumped at +5%, only 15%
                    # rode (manual sell, missed ~$70). 0.65 keeps giveback protection (65%
                    # locked at +5%; remnant trails near peak on faders) while leaving a 35%
                    # runner slice that the new peak-scaled trail lets run on moonshots (2026-06-09).
                    # CONVEX tier (2026-06-10): tiny TP1 partial (10%) — the 90%
                    # remainder rides the peak-scaled trail. The elites' payoff
                    # is the tail (winners p90 +107%); the 0.65 bank-the-pop
                    # override is right for consensus fires, wrong for convex.
                    # 2026-06-11 exit replay (120 post-gate closes, peaks recorded):
                    # banking 0.65 at TP1 capped our own winners — replayed
                    # +2.35%/tr vs +3.37 at 0.35 (monotonic toward smaller
                    # fractions; the convex tier tests 0.10). Fade risk now
                    # covered by runner-tilt trail + elite-exit + stop-grace.
                    # Env SMART_FOLLOW_TP1_FRACTION to retune without deploy.
                    tp1_sell_override=(0.10
                                       if getattr(pos, "strategy", "") == "smart_follow_convex"
                                       else _follow_tp1_fraction()
                                       if getattr(pos, "strategy", "").startswith("smart_follow")
                                       else None),
                    follow_origin=getattr(pos, "strategy", "").startswith("smart_follow"),
                    # convex NEVER takes stop-grace: the decode says cut fast
                    # (their own median loser exit = -15.2% ≈ our -15 dip stop)
                    stop_grace=(getattr(pos, "strategy", "").startswith("smart_follow")
                                and getattr(pos, "strategy", "") != "smart_follow_convex"
                                and _stop_grace_arm(addr)),
                    tp1_hit=bool(getattr(pos, "take_profit_1_hit", False)),
                    tp2_hit=bool(getattr(pos, "take_profit_2_hit", False)),
                    current_price=entry_px,
                    # Restore the TRUE peak from the persisted Position (2026-06-10):
                    # resetting peak to entry on every restart re-armed post-TP1
                    # trails (each deploy wiped the peak -> the remainder needed a
                    # fresh giveback from a NEW lower peak to close). With ~12
                    # deploys in a day, riders sat open 19h+ (MINER/ZOOMER).
                    peak_price=entry_px * (1 + max(0.0,
                        float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0)) / 100.0),
                    min_price_usd=(float(getattr(pos, "min_price_usd", 0.0) or 0.0)
                                   or entry_px),
                    pyramid_signal_score=getattr(pos, "signal_score", 0),
                    hh_hl_confirmed=getattr(pos, "hh_hl_confirmed", False),
                    scalp_meta=getattr(pos, "scalp_meta", None),
                    pair_address=getattr(pos, "pair_address", "") or "",
                )
                # Peak recorder init — additive, shadow-only
                try:
                    from core.peak_recorder import get_recorder
                    get_recorder().init_position(
                        token_address=addr,
                        token_symbol=getattr(pos, "token_symbol", "?"),
                        pair_address=getattr(pos, "pair_address", "") or "",
                        entry_price=entry_px,
                        entry_time=getattr(pos, "entry_time", datetime.now(timezone.utc)),
                        entry_meta=getattr(pos, "entry_meta", None),
                    )
                except Exception:
                    pass

        # Update prices and evaluate each position
        for addr, state in list(self._states.items()):
            await self._update_price(addr, state)
            if addr in self._states:
                await self._evaluate_position(addr, state)

    # ───────────────────────────────────────────────────────────
    # Shadow-mode chart-reader signal-flip detector for dip_buy.
    # Logged only, not enforced. See PositionState.signal_flip_*
    # field comments for rationale.
    # ───────────────────────────────────────────────────────────
    # 2026-05-15: dropped 60s → 15s so peak_recorder accumulates minute
    # records on shorter trades. Was: all overnight 2026-05-15 traces
    # had minutes=0 because most trades closed in <60s of chart_flip
    # cadence ticks, so record_minute never fired. 15s gives ~32 calls
    # over an 8min PAC trade → ~8 unique minute records (one per minute
    # boundary). Chart_signal_flip itself still uses `_CHART_FLIP_BEARISH
    # _PHASES_REQUIRED`-based consecutive counter, which works fine at
    # any cadence.
    _CHART_FLIP_CHECK_INTERVAL_S: float = 15.0
    _CHART_FLIP_BEARISH_PHASES_REQUIRED: int = 2

    async def _maybe_check_chart_signal_flip(
        self, token_address: str, state: PositionState, pnl_pct: float
    ):
        """Periodic chart_reader re-eval while a position is open.

        Cadence: every 60s while position is held. Cost: one
        assemble_chart_data call (DexScreener primary) + chart_reader
        composite — ~500ms wall-clock when DexScreener cache is cold,
        free when warm.

        Bearish flip = at least N of the following phases are decisively
        bearish on the 5m timeframe:
          - chart_score drops below 30 (was probably > 50 at entry)
          - chart_structure_5m_verdict in {REVERSAL_DOWN, TREND_DOWN}
          - chart_sweep_5m_verdict == BEARISH_SWEEP
          - chart_trendline_5m_verdict == BREAKDOWN
          - chart_pattern_5m_dir == 'bearish' (engulfing/shooting-star)

        On first bearish flip we record the timestamp + pnl_pct on the
        position state. Both end up in entry_meta on sell so we can
        validate "would early exit have helped?" via the 10pm pipeline.
        """
        if state.strategy != "dip_buy":
            return
        if not state.pair_address:
            return  # Legacy position, no pair info
        if self.dexs_client is None and self.gt_client is None:
            return  # No source plumbed; silent skip

        now = time.monotonic()
        if (now - state.last_chart_check_mono) < self._CHART_FLIP_CHECK_INTERVAL_S:
            return
        state.last_chart_check_mono = now

        # Lazy imports — keeps module load light when feature is disabled.
        try:
            from feeds.chart_data import assemble_chart_data
            from feeds.chart_reader import read_chart
        except Exception:
            return

        # Fetch chart_data first so the peak_recorder can record a minute
        # even if read_chart() (composite signal computation) errors. The
        # recorder only needs the raw candles, not the chart_reader output.
        cd = None
        try:
            cd = await assemble_chart_data(
                self.gt_client, state.pair_address,
                dexs_client=self.dexs_client,
            )
        except Exception as e:
            logger.debug(f"[PositionManager] chart_data err for {state.token_symbol}: {e}")

        # Peak recorder — feed candles independently of chart_reader success
        try:
            from core.peak_recorder import get_recorder
            if cd is not None and (cd.candles_1m or []):
                get_recorder().record_minute(
                    token_address=state.token_address,
                    candles_1m=cd.candles_1m or [],
                    candles_5m=cd.candles_5m or [],
                    candles_15m=cd.candles_15m or [],
                )
        except Exception:
            pass

        # Now compute composite verdict for the chart-flip detector.
        try:
            ctx = await read_chart(
                self.gt_client, state.pair_address, chart_data=cd,
            )
        except Exception as e:
            logger.debug(f"[PositionManager] read_chart err for {state.token_symbol}: {e}")
            return
        if ctx is None:
            return

        # Stamp the chart features cache for the smart-TP tick logger.
        # Fail-safe: only the read; we never reraise if a field is missing.
        try:
            _last_5m = (cd.candles_5m or [])[-1] if cd and cd.candles_5m else None
            _last_5m_dir = None
            if _last_5m and _last_5m.get("close") is not None and _last_5m.get("open") is not None:
                _last_5m_dir = "green" if _last_5m["close"] >= _last_5m["open"] else "red"
            state.chart_features_cache = {
                "composite_score": float(getattr(ctx, "composite_score", 0.0) or 0.0),
                "mtf_score": float((ctx.mtf or {}).get("score") or 0.0),
                "mtf_alignment": (ctx.mtf or {}).get("alignment"),
                "struct_5m_verdict": (ctx.structure_5m or {}).get("structure_verdict"),
                "struct_5m_state": (ctx.structure_5m or {}).get("state"),
                "sweep_5m_verdict": (ctx.sweeps_5m or {}).get("sweep_verdict"),
                "trendline_5m_verdict": (ctx.trendlines_5m or {}).get("trendline_verdict"),
                "pattern_5m_dir": (ctx.pattern_5m or {}).get("direction"),
                "pattern_5m_conf": float((ctx.pattern_5m or {}).get("confidence") or 0.0),
                "vp_above_poc": (ctx.volume_profile_5m or {}).get("above_poc"),
                "sr_at_resistance": (ctx.sr_5m or {}).get("at_resistance"),
                "sr_at_support": (ctx.sr_5m or {}).get("at_support"),
                "last_5m_dir": _last_5m_dir,
            }
            state.chart_features_cache_ts = time.time()
        except Exception:
            pass

        bearish: List[str] = []
        score = float(getattr(ctx, "composite_score", 50.0) or 50.0)
        if score < 30.0:
            bearish.append(f"score={score:.0f}<30")
        struct_v = (ctx.structure_5m or {}).get("structure_verdict")
        if struct_v in ("REVERSAL_DOWN", "TREND_DOWN"):
            bearish.append(f"struct_5m={struct_v}")
        sweep_v = (ctx.sweeps_5m or {}).get("sweep_verdict")
        if sweep_v == "BEARISH_SWEEP":
            bearish.append("sweep_5m=BEARISH_SWEEP")
        tl_v = (ctx.trendlines_5m or {}).get("trendline_verdict")
        if tl_v == "BREAKDOWN":
            bearish.append(f"trendline_5m={tl_v}")
        pat_dir = (ctx.pattern_5m or {}).get("direction")
        if pat_dir == "bearish":
            bearish.append("pattern_5m=bearish")

        is_flip = len(bearish) >= self._CHART_FLIP_BEARISH_PHASES_REQUIRED
        if is_flip:
            state.consecutive_bearish_flips += 1
            if state.signal_flip_first_ts is None:
                state.signal_flip_first_ts = datetime.now(timezone.utc)
                state.signal_flip_first_pnl = pnl_pct
                state.signal_flip_reasons = bearish
                logger.info(
                    f"[PositionManager/{self.chain_name}] SHADOW signal-flip BEARISH "
                    f"{state.token_symbol} pnl={pnl_pct:+.1f}% phases={','.join(bearish)} "
                    f"score={score:.0f}"
                )
            elif state.consecutive_bearish_flips % 5 == 0:
                # Throttled re-log every 5 consecutive bearish reads
                logger.info(
                    f"[PositionManager/{self.chain_name}] SHADOW signal-flip STILL BEARISH "
                    f"{state.token_symbol} pnl={pnl_pct:+.1f}% n_consec={state.consecutive_bearish_flips}"
                )
        else:
            state.consecutive_bearish_flips = 0

    # ───────────────────────────────────────────────────────────
    # 1s cascade detector — SHADOW 2026-05-11
    # ───────────────────────────────────────────────────────────
    _CASCADE_1S_CHECK_INTERVAL_S: float = 30.0  # check every 30s during hold

    async def _maybe_check_1s_cascade(
        self, state: PositionState, pnl_pct: float
    ):
        """Periodic 1s-cascade detector while a position is open. SHADOW only.

        Pattern: the 1s structure shows a fast cascade in the last 60s —
        volatile range, majority-red bars, close near low. If detected,
        log the wall-clock time + pnl at first detection.

        Persisted into entry_meta on sell so we can later quantify
        "would a 1s-cascade exit have saved $" without running it live.

        Criteria (all must hold):
          - bars_60s >= 3 (enough data)
          - range_pct_60s > 2.0% (active volatility)
          - close_pos_60s < 0.25 (close near bottom of recent range)
          - red_pct_60s > 0.5 (majority-red bars)

        Cost: 1 HTTP per position per 30s during hold (~120 HTTPs/hour for
        a single position). Cap with _CASCADE_1S_CHECK_INTERVAL_S.
        """
        if state.strategy != "dip_buy":
            return
        if not state.pair_address:
            return

        now = time.monotonic()
        if (now - state.last_1s_cascade_check_mono) < self._CASCADE_1S_CHECK_INTERVAL_S:
            return
        state.last_1s_cascade_check_mono = now

        try:
            from feeds.dexscreener_chart_format import parse_chart_bars
        except Exception:
            return

        # DexScreener uses TLS fingerprinting — aiohttp gets 403. Must
        # use curl_cffi with impersonate='chrome' wrapped in to_thread().
        slug = None
        SOL_QUOTE = "So11111111111111111111111111111111111111112"

        def _fetch_slug_and_bars_sync():
            try:
                from curl_cffi import requests as _cf
                # Resolve dex slug from pair-info
                r_pair = _cf.get(
                    f"https://api.dexscreener.com/latest/dex/pairs/solana/{state.pair_address}",
                    impersonate="chrome", timeout=5,
                )
                if r_pair.status_code != 200:
                    return None, None
                d = r_pair.json()
                pp = d.get("pairs") or ([d.get("pair")] if d.get("pair") else [])
                if not pp:
                    return None, None
                raw = (pp[0].get("dexId") or "").lower()
                _slug = {
                    "pumpswap": "pumpfundex", "pumpfun": "pumpfundex",
                    "raydium": "solamm", "meteora": "meteora",
                }.get(raw, raw or "pumpfundex")
                # Fetch bars
                _url = (
                    f"https://io.dexscreener.com/dex/chart/amm/v3/{_slug}"
                    f"/bars/solana/{state.pair_address}?res=1S&cb=999&q={SOL_QUOTE}"
                )
                r_bars = _cf.get(
                    _url, impersonate="chrome", timeout=5,
                    headers={
                        "Origin": "https://dexscreener.com",
                        "Referer": "https://dexscreener.com/",
                    },
                )
                if r_bars.status_code == 200:
                    return _slug, r_bars.content
                return _slug, None
            except Exception:
                return None, None

        try:
            from feeds.dexscreener_client import run_ds_fetch
            _out = await run_ds_fetch(_fetch_slug_and_bars_sync)
            if _out is None:
                return None   # DS circuit open — caller falls back
            slug, bars_raw = _out
        except Exception:
            return
        if not slug or not bars_raw:
            return

        bars = parse_chart_bars(bars_raw)
        now_ms = int(time.time() * 1000)
        pre60 = [b for b in bars if now_ms - 60000 <= b["ts_ms"] < now_ms]
        if len(pre60) < 3:
            return

        h = max(b["high"] for b in pre60)
        l = min(b["low"] for b in pre60)
        mid = (h + l) / 2
        range_pct = (h - l) / mid * 100 if mid > 0 else 0
        red_n = sum(1 for b in pre60 if b["close"] < b["open"])
        red_pct = red_n / len(pre60)
        last_close = pre60[-1]["close"]
        close_pos = (last_close - l) / (h - l) if h > l else 0.5

        reasons = []
        if range_pct > 2.0:
            reasons.append(f"range={range_pct:.2f}%>2.0")
        if close_pos < 0.25:
            reasons.append(f"close_pos={close_pos:.2f}<0.25")
        if red_pct > 0.5:
            reasons.append(f"red_pct={red_pct*100:.0f}%>50")

        is_cascade = (
            len(pre60) >= 3
            and range_pct > 2.0
            and close_pos < 0.25
            and red_pct > 0.5
        )
        if is_cascade:
            state.cascade_1s_consec += 1
            if state.cascade_1s_first_ts is None:
                state.cascade_1s_first_ts = datetime.now(timezone.utc)
                state.cascade_1s_first_pnl = pnl_pct
                state.cascade_1s_reasons = reasons
                logger.info(
                    f"[PositionManager/{self.chain_name}] SHADOW 1s-cascade DETECTED "
                    f"{state.token_symbol} pnl={pnl_pct:+.1f}% "
                    f"reasons={','.join(reasons)}"
                )
            elif state.cascade_1s_consec % 5 == 0:
                logger.info(
                    f"[PositionManager/{self.chain_name}] SHADOW 1s-cascade STILL ACTIVE "
                    f"{state.token_symbol} pnl={pnl_pct:+.1f}% n_consec={state.cascade_1s_consec}"
                )
        else:
            state.cascade_1s_consec = 0

    async def _fetch_volume_snapshot(self, token_address: str):
        """
        Pull DexScreener volume data (h24, h1, m5) for the best Solana pair.
        Returns (v_h24, v_h1, v_m5) or None on failure. Used by the volume-
        death exit check so we don't rely on stale cached values.
        """
        try:
            url = f"{DEXSCREENER_TOKEN}{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            pairs = [
                p for p in (data.get("pairs") or [])
                if (p.get("chainId") or "").lower() == "solana"
            ]
            if not pairs:
                return None
            best = max(
                pairs,
                key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0),
            )
            vol = best.get("volume") or {}
            return (
                float(vol.get("h24") or 0),
                float(vol.get("h1") or 0),
                float(vol.get("m5") or 0),
            )
        except Exception:
            return None

    async def _fetch_jupiter_price(self, token_address: str) -> float:
        """
        Poll Jupiter Price API for near-instant AMM price.
        Much faster than DexScreener — queries the AMM state directly.
        Returns 0.0 on failure.
        """
        try:
            url = f"https://api.jup.ag/price/v2?ids={token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status != 200:
                        return 0.0
                    data = await resp.json()
                    item = (data.get("data") or {}).get(token_address) or (data.get("data") or {}).get(token_address.lower())
                    if item:
                        return float(item.get("price", 0) or 0)
        except Exception:
            pass
        return 0.0

    async def _update_price(self, token_address: str, state: PositionState):
        """
        Fetch current price via fastest available source:
          1. Axiom cache (<5s)  — socket8 WS, near real-time
          2. DexScreener cache (<3s) — PriceFeed polls every 1s
          3. Jupiter Price API — queries AMM directly, sub-second, throttled to 2s
          4. DexScreener REST — last resort, throttled to once per 5s
        Volume/liquidity for stall detection come from the REST call (throttled).
        """
        now_ts = time.time()
        addr_lower = token_address.lower()

        # ── Fast path 1: Axiom real-time cache ───────────────────────────────
        live_price = 0.0
        if self.axiom_price_feed is not None:
            _p = self.axiom_price_feed.price_cache.get(addr_lower, 0)
            _t = self.axiom_price_feed.price_timestamps.get(addr_lower, 0)
            if _p > 0 and (now_ts - _t) < 5.0:
                live_price = _p
                self._apply_price_update(
                    token_address, state, live_price,
                    volume_h1=self.axiom_price_feed.volume_cache.get(addr_lower, state.current_volume_usd),
                    volume_m5=state.current_m5_volume,
                    liquidity_usd=self.axiom_price_feed.liquidity_cache.get(addr_lower, state.current_liquidity_usd),
                )

        # ── Fast path 2: Solana RPC + Jupiter 0.5s-poll cache ────────────────
        # Covers all pool types: Pump.fun (direct RPC), PumpSwap, Raydium,
        # Meteora, Orca, LaunchLab (Jupiter API). ~1s latency vs 5-15s DexScreener.
        if live_price <= 0 and self.rpc_price_feed is not None:
            _p = self.rpc_price_feed.price_cache.get(addr_lower, 0)
            _t = self.rpc_price_feed.price_timestamps.get(addr_lower, 0)
            if _p > 0 and (now_ts - _t) < 2.0:
                live_price = _p
                self._apply_price_update(
                    token_address, state, live_price,
                    volume_h1=state.current_volume_usd,
                    volume_m5=state.current_m5_volume,
                    liquidity_usd=state.current_liquidity_usd,
                )

        # ── Fast path 3: DexScreener 1s-poll cache ───────────────────────────
        if live_price <= 0 and self.dex_price_feed is not None:
            _p = self.dex_price_feed.price_cache.get(addr_lower, 0)
            _t = self.dex_price_feed.price_timestamps.get(addr_lower, 0)
            if _p > 0 and (now_ts - _t) < 3.0:
                live_price = _p
                self._apply_price_update(
                    token_address, state, live_price,
                    volume_h1=self.dex_price_feed.volume_cache.get(addr_lower, state.current_volume_usd),
                    volume_m5=state.current_m5_volume,
                    liquidity_usd=self.dex_price_feed.liquidity_cache.get(addr_lower, state.current_liquidity_usd),
                )

        # ── Fast path 4: Jupiter Price API (throttled to 2s) ─────────────────
        # Fallback when RPC feed hasn't seen the token yet
        last_jup = self._last_rest_fetch.get(f"jup_{addr_lower}", 0)
        if live_price <= 0 and (now_ts - last_jup) >= 2.0:
            self._last_rest_fetch[f"jup_{addr_lower}"] = now_ts
            jup_price = await self._fetch_jupiter_price(token_address)
            if jup_price > 0:
                live_price = jup_price
                self._apply_price_update(token_address, state, live_price,
                                         volume_h1=state.current_volume_usd,
                                         volume_m5=state.current_m5_volume,
                                         liquidity_usd=state.current_liquidity_usd)

        # ── REST: volume/liquidity refresh (throttled to 5s) ─────────────────
        last_rest = self._last_rest_fetch.get(addr_lower, 0)
        if (now_ts - last_rest) < 5.0:
            return
        self._last_rest_fetch[addr_lower] = now_ts

        try:
            url = f"{DEXSCREENER_TOKEN}{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    pairs = [
                        p for p in data.get("pairs", [])
                        if p.get("chainId") == self.chain_id
                    ]
                    if not pairs:
                        return
                    pair = max(
                        pairs,
                        key=lambda p: p.get("liquidity", {}).get("usd", 0)
                    )
                    rest_price = float(pair.get("priceUsd", 0) or 0)
                    volume_data = pair.get("volume", {})
                    volume_h1 = volume_data.get("h1", 0) or 0
                    volume_m5 = volume_data.get("m5", 0) or 0
                    liquidity_usd = float(
                        (pair.get("liquidity") or {}).get("usd", 0) or 0
                    )

                    # Use REST price only if faster sources had nothing fresh
                    price_to_apply = live_price if live_price > 0 else rest_price
                    if price_to_apply > 0:
                        self._apply_price_update(token_address, state, price_to_apply,
                                                 volume_h1, volume_m5, liquidity_usd)

                    # Transaction rate tracking — used for txn collapse exit signal
                    _txn_buys  = pair.get("txns", {}).get("h1", {}).get("buys",  0)
                    _txn_sells = pair.get("txns", {}).get("h1", {}).get("sells", 0)
                    _total_txns = _txn_buys + _txn_sells
                    state.current_txns_h1 = _total_txns
                    if state.entry_txns_h1 == 0 and _total_txns > 0:
                        state.entry_txns_h1 = _total_txns

                    # Live bs_h1 / bs_m5 — synced to Position so sell paths can
                    # capture order-flow state at exit time (catches "we sold
                    # while buyers were returning" pattern).  Cap at 999 to
                    # avoid +inf serialization.
                    _txn_b_m5 = pair.get("txns", {}).get("m5", {}).get("buys",  0)
                    _txn_s_m5 = pair.get("txns", {}).get("m5", {}).get("sells", 0)
                    def _bs_ratio(b, s):
                        if s > 0:
                            return min(b / s, 999.0)
                        if b > 0:
                            return 999.0
                        return 0.0
                    if token_address in self.open_positions_ref:
                        tp = self.open_positions_ref[token_address]
                        tp.current_bs_h1 = _bs_ratio(_txn_buys, _txn_sells)
                        tp.current_bs_m5 = _bs_ratio(_txn_b_m5, _txn_s_m5)

                    # Volume window for stall detection (uses REST data only)
                    last_check = self._last_volume_check.get(token_address)
                    if (not last_check or
                            (datetime.now(timezone.utc) - last_check).total_seconds()
                            >= self.stall_interval * 60):
                        state.add_volume_window(
                            volume_h1,
                            buys=_txn_buys,
                            sells=_txn_sells
                        )
                        self._last_volume_check[token_address] = datetime.now(timezone.utc)

        except Exception as e:
            logger.debug(f"[PositionManager/{self.chain_name}] Price REST: {e}")

    def _spike_should_accept(self, token_address: str, new_price: float,
                              ref_price: float, token_symbol: str) -> bool:
        """Anti-corruption guard with confirmation-based accept.

        Returns True if the price update should be accepted. Returns False
        if it should be rejected as a likely feed glitch.

        Rules:
          - Within ±20% of ref_price → accept immediately, clear pending.
          - >20% deviation but matches a recent rejected price (within 5%,
            <60s window) → increment confirmation count. Accept after 3
            confirmations OR 30s of sustained rejection.
          - Otherwise → reject and start tracking.

        Real fast pumps (HANTA-Kun 2026-05-08: +106% legit) confirm in
        <1s because the WS streams many ticks per second at the new price.
        Single-tick glitches (Goblin 2026-04-27: -94% one-off polled read)
        never accumulate confirmations and stay rejected.
        """
        SPIKE_UP = 1.20
        SPIKE_DOWN = 0.80
        SAME_PRICE_TOL = 0.05
        CONFIRM_COUNT = 3
        CONFIRM_WINDOW = 60.0
        SUSTAIN_SEC = 30.0

        if ref_price <= 0 or new_price <= 0:
            return True

        ratio = new_price / ref_price
        if SPIKE_DOWN <= ratio <= SPIKE_UP:
            self._spike_pending.pop(token_address, None)
            return True

        now = time.time()
        pending = self._spike_pending.get(token_address)
        pct = (ratio - 1) * 100
        sign = "+" if pct >= 0 else ""

        if pending:
            same_price = abs(new_price / pending["price"] - 1) < SAME_PRICE_TOL
            elapsed = now - pending["first_seen"]
            in_window = elapsed < CONFIRM_WINDOW
            if same_price and in_window:
                pending["count"] += 1
                if pending["count"] >= CONFIRM_COUNT or elapsed >= SUSTAIN_SEC:
                    logger.info(
                        f"[PositionManager/{self.chain_name}] ✓ Price spike "
                        f"CONFIRMED after {pending['count']} ticks / "
                        f"{elapsed:.1f}s: {token_symbol} → {new_price:.8f} "
                        f"(ref was {ref_price:.8f}, {sign}{pct:.0f}%) — "
                        f"accepting as legitimate move"
                    )
                    self._spike_pending.pop(token_address, None)
                    return True
                return False  # still confirming, silent

        # New rejection (no pending, different price, or expired window)
        self._spike_pending[token_address] = {
            "price": new_price,
            "count": 1,
            "first_seen": now,
        }
        logger.warning(
            f"[PositionManager/{self.chain_name}] ⚠️  Price spike rejected "
            f"(1/{CONFIRM_COUNT}, awaiting confirmation): {token_symbol} "
            f"{ref_price:.8f} → {new_price:.8f} ({sign}{pct:.1f}% "
            f"single tick) — likely corrupted feed data"
        )
        return False

    def _apply_price_update(self, token_address: str, state: PositionState,
                             price: float, volume_h1: float,
                             volume_m5: float, liquidity_usd: float):
        """Apply a price update to state and sync back to the open position object."""
        # Sanity gate: reject single-tick price moves >20% in EITHER direction
        # via _spike_should_accept (confirmation-based — see helper docstring).
        # Aligns with the realtime stop gate in check_stop_loss_realtime so
        # corrupted ticks can't slip through one path while being rejected on
        # the other. Falls back to peak_price then entry_price when
        # current_price is unset (first tick).
        #
        # Downside gate added 2026-05-03: prior version only rejected upticks,
        # so a glitched -100% feed read (Goblin entry $1.030 → polled tick
        # $0.00319) wrote state.current_price = $0.00319 and the polled
        # _check_stops_and_exits then fired a phantom -99.7% stop. The
        # realtime gate had been catching the WS-path glitches; polled REST
        # leaked. Both directions now mirror.
        #
        # Confirmation logic added 2026-05-08: the original gate permanently
        # rejected real fast pumps (HANTA-Kun +106% legit move). Helper now
        # accepts after N=3 confirmations / 30s sustained.
        ref_price = (
            state.current_price if state.current_price > 0
            else state.peak_price if state.peak_price > 0
            else state.entry_price
        )
        if not self._spike_should_accept(
                token_address, price, ref_price, state.token_symbol):
            return
        state.current_price = price
        state.current_volume_usd = volume_h1
        state.current_h1_volume = volume_h1
        state.current_m5_volume = volume_m5
        state.current_liquidity_usd = liquidity_usd
        if price > state.peak_price:
            state.peak_price = price
        if state.min_price_usd <= 0 or price < state.min_price_usd:
            state.min_price_usd = price

        # Update liquidity confirmed flag
        MIN_EXIT_LIQUIDITY = 1000
        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        if (state.liquidity_confirmed
                and liquidity_usd < MIN_EXIT_LIQUIDITY
                and price <= 0          # only flag dead if price is also gone
                and age_seconds > 15):
            if state.dead_liquidity_since is None:
                state.dead_liquidity_since = datetime.now(timezone.utc)
        elif liquidity_usd >= MIN_EXIT_LIQUIDITY or price > 0:
            # Price > 0 means token is tradeable — bonding curve tokens show $0
            # LP liquidity on DexScreener but are actively priced via Jupiter/RPC.
            state.dead_liquidity_since = None

        # Sync live price/PnL back to the trader Position object
        if token_address in self.open_positions_ref:
            tp = self.open_positions_ref[token_address]
            tp.current_price_usd = price
            tp.current_price_ts = time.time()
            # Sync min_price for max-drawdown calc at sell time.  Without this,
            # tp.min_price_usd stayed at entry_price and drawdown read 0.
            if state.min_price_usd > 0:
                tp.min_price_usd = state.min_price_usd
            if state.entry_price > 0:
                tp.pnl_usd = (
                    (price / state.entry_price - 1)
                    * getattr(tp, "amount_usd", state.position_size_usd)
                )
                # Track max favorable excursion during hold
                pnl_pct = (price / state.entry_price - 1) * 100
                prev_peak = getattr(tp, "peak_pnl_pct", 0.0) or 0.0
                if pnl_pct > prev_peak:
                    tp.peak_pnl_pct = pnl_pct
                    entry_mono = getattr(tp, "entry_time_monotonic", 0) or 0
                    tp.peak_pnl_at_secs = (
                        int(time.monotonic() - entry_mono) if entry_mono > 0 else 0
                    )
                # Hold-time pnl snapshots — capture once per threshold crossing
                # so we can validate stale-exit hypotheses on forward data.
                # Also fire a holder-snapshot task at each threshold to measure
                # mid-hold distribution velocity (entry -> 30m -> 60m -> exit).
                _age_s = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
                if tp.hold_pnl_snapshots is None:
                    tp.hold_pnl_snapshots = {}
                for _label, _thresh_s in (("30m", 1800), ("60m", 3600), ("90m", 5400), ("120m", 7200)):
                    if _age_s >= _thresh_s and _label not in tp.hold_pnl_snapshots:
                        tp.hold_pnl_snapshots[_label] = round(pnl_pct, 2)
                        logger.info(
                            f"[PositionManager/{self.chain_name}] ⏱ HOLD SNAPSHOT: "
                            f"{state.token_symbol} @ {_label} pnl={pnl_pct:+.1f}%"
                        )
                        # Fire holder + orderflow snapshots in the background —
                        # never block the price-tick path. Trader resolves the
                        # position by address and writes to the parallel
                        # snapshot dicts on Position. holder_snapshot also
                        # populates rugcheck_score_snapshots and lp_snapshots
                        # from the same rugcheck call (gaps 2 + 4).
                        try:
                            asyncio.create_task(
                                self.trader.capture_holder_snapshot(token_address, _label)
                            )
                            asyncio.create_task(
                                self.trader.capture_orderflow_snapshot(token_address, _label)
                            )
                        except Exception:
                            pass

        # Set entry volume baseline on first update
        if state.entry_volume_usd == 0 and volume_h1 > 0:
            state.entry_volume_usd = volume_h1
            logger.info(
                f"[PositionManager/{self.chain_name}] "
                f"📊 Baseline set: {state.token_symbol} "
                f"${volume_h1:,.0f}/hr — "
                f"stall threshold: "
                f"{state.stall_threshold*100:.0f}% "
                f"(${volume_h1 * state.stall_threshold:,.0f}/hr)"
            )

    async def _evaluate_position(self, token_address: str, state: PositionState):
        """Check all exit and management rules for one position."""
        if state.current_price <= 0 or state.entry_price <= 0:
            return

        # Position-tick logger — fires every evaluation cycle. Fail-safe.
        # Drives the smart-TP project (mine peak-giveback predictors from
        # full hold-time tick traces).
        try:
            _tp_obj = self.open_positions_ref.get(token_address)
            self._log_position_tick(token_address, state, _tp_obj)
        except Exception:
            pass

        # Smart-TP peak detector (SHADOW) — log WOULD-SELL when topping
        # signals stack while in green. ~10 lines, zero risk to live exits.
        try:
            _pnl_pct_now = (state.current_price / state.entry_price - 1) * 100.0 \
                if state.entry_price > 0 else 0.0
            self._maybe_peak_detect_shadow(state, _pnl_pct_now)
        except Exception:
            pass

        MIN_EXIT_LIQUIDITY = 1000
        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()

        # ── DEAD LIQUIDITY — write off if liquidity gone for >60s ────────────
        if (state.liquidity_confirmed
                and state.current_liquidity_usd < MIN_EXIT_LIQUIDITY
                and state.dead_liquidity_since is not None
                and age_seconds > 15):
            dead_seconds = (
                datetime.now(timezone.utc) - state.dead_liquidity_since
            ).total_seconds()
            # If price is still live, this is a DexScreener data gap — not a rug.
            # Pump.fun bonding curve tokens report $0 LP liquidity because their
            # liquidity lives in the bonding curve contract, not a traditional pool.
            # Real rugs kill price AND liquidity simultaneously.
            if state.current_price > 0:
                logger.warning(
                    f"[PositionManager/{self.chain_name}] ⚠️ Dead liquidity (price live): "
                    f"{state.token_symbol} — ${state.current_liquidity_usd:.0f} liq "
                    f"but price=${state.current_price:.8f} — likely bonding curve or "
                    f"pool migration, holding"
                )
                return
            logger.warning(
                f"[PositionManager/{self.chain_name}] ⚠️ Dead liquidity: "
                f"{state.token_symbol} — ${state.current_liquidity_usd:.0f} "
                f"(need ${MIN_EXIT_LIQUIDITY:,}) — waiting up to 60s"
            )
            if dead_seconds < 60:
                return  # Give it 60s to recover
            logger.warning(
                f"[PositionManager/{self.chain_name}] 💀 FULL LOSS (dead liquidity): "
                f"{state.token_symbol} — liquidity gone for "
                f"{dead_seconds/60:.1f} min — writing off ${state.position_size_usd:.0f}"
            )
            state.current_price = 0.0
            state.liquidity_confirmed = False
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason="Dead liquidity — full loss"
            )
            return

        # Recompute age for the rest of evaluation
        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        pnl_pct = state.pnl_pct

        # ═══════════════════════════════════════════════════════════════
        # MICRO-CAP POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════
        if state.is_micro_cap:

            # ── MC TIME STOP — exit if not positive at 10 minutes ────────
            if (not state.tp1_hit
                    and age_seconds >= 600
                    and pnl_pct <= 0):
                logger.warning(
                    f"[PositionManager/{self.chain_name}] ⏱ MC TIME STOP: "
                    f"{state.token_symbol} — not positive after "
                    f"{age_seconds/60:.0f}min ({pnl_pct:+.1f}%)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC time stop — not positive at 10min"
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=1800  # 30min cooldown for MC time stop
                    )
                return

            # ── MC HARD EXPIRY — 20-minute absolute cap before TP1 ───────
            # 73% of post-graduation tokens collapse below migration price
            # within 20 minutes (MemeTrans 2026). If TP1 hasn't fired in
            # 20 minutes the token isn't moving — exit at any P&L.
            if not state.tp1_hit and age_seconds >= 1200:
                logger.warning(
                    f"[PositionManager/{self.chain_name}] ⏱ MC 20MIN EXPIRY: "
                    f"{state.token_symbol} — no TP1 after 20min ({pnl_pct:+.1f}%)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC 20min expiry — no momentum"
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=1800
                    )
                return

            # ── MC WINNER TRAIL — close if drops mc_winner_trail_pct% from peak
            _MIN_PEAK_GAIN_FOR_TRAIL = 5.0
            _peak_gain_pct = (state.peak_price - state.entry_price) / state.entry_price * 100
            if (state.peak_price > 0
                    and _peak_gain_pct >= _MIN_PEAK_GAIN_FOR_TRAIL
                    and state.current_price <= state.peak_price * (1 - self.mc_winner_trail_pct / 100)):
                drop_from_peak = (state.peak_price - state.current_price) / state.peak_price * 100
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🔒 MC WINNER TRAIL: "
                    f"{state.token_symbol} -{drop_from_peak:.1f}% from peak"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC winner trail -{drop_from_peak:.1f}% from peak"
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=14400  # 4h — token already ran, don't re-enter
                    )
                return

            # ── MC STOP LOSS ──────────────────────────────────────────────
            # Skip if realtime feed already claimed this position — prevents
            # a duplicate polling-loop sell racing against the realtime ensure_future.
            if token_address in self._stop_triggered:
                return
            _mc_stop = 35.0 if state.strategy == "graduation" else self.mc_stop_loss_pct
            if pnl_pct <= -_mc_stop:
                age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
                is_flash_crash = age_seconds <= 120
                logger.warning(
                    f"[PositionManager/{self.chain_name}] 🛑 MC STOP LOSS: "
                    f"{state.token_symbol} at {pnl_pct:.1f}%"
                    + (" ⚠️ FLASH CRASH — possible rug" if is_flash_crash else "")
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC stop loss -{_mc_stop:.0f}%"
                )
                self.stop_loss_hits += 1
                cooldown = 86400 if is_flash_crash else 14400
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=cooldown
                    )
                return

            # ── MC TXN COLLAPSE — exit if txns/hr fell to <10% of entry ─────
            # Leading indicator: txn rate dies before price confirms the dump.
            # Require ≥50 entry txns to avoid false signals on thin tokens.
            # Only fires pre-TP1 after a 5-minute stabilization window.
            if (not state.tp1_hit
                    and state.entry_txns_h1 >= 50
                    and state.current_txns_h1 > 0
                    and state.current_txns_h1 < state.entry_txns_h1 * 0.10
                    and age_seconds >= 300):
                logger.info(
                    f"[PositionManager/{self.chain_name}] 📉 MC TXN COLLAPSE: "
                    f"{state.token_symbol} — {state.current_txns_h1} txns/hr "
                    f"vs {state.entry_txns_h1} at entry "
                    f"({state.current_txns_h1 / state.entry_txns_h1 * 100:.0f}% of baseline)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC txn collapse — {state.current_txns_h1}/hr vs {state.entry_txns_h1}/hr"
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=3600
                    )
                return

            # ── MC TAKE PROFIT TIERS ──────────────────────────────────────
            if pnl_pct >= self.mc_tp3_pct and not state.tp3_hit:
                state.tp3_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 MC TP3: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.mc_tp3_sell,
                    reason=f"MC TP3 +{pnl_pct:.1f}%"
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=14400  # 4h — token already ran
                    )
                return

            if pnl_pct >= self.mc_tp2_pct and not state.tp2_hit:
                state.tp2_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 MC TP2: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.mc_tp2_sell,
                    reason=f"MC TP2 +{pnl_pct:.1f}%"
                )
                return

            if pnl_pct >= self.mc_tp1_pct and not state.tp1_hit:
                state.tp1_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 MC TP1: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.mc_tp1_sell,
                    reason=f"MC TP1 +{pnl_pct:.1f}%"
                )
                return

            # MC stall check
            if (not state.stall_exit_done
                    and state.hours_open >= self.stall_min_hours
                    and state.is_stalled):
                m5_rate = state.current_m5_volume * 12
                threshold = state.entry_volume_usd * state.stall_threshold
                logger.info(
                    f"[PositionManager/{self.chain_name}] 😴 MC STALL: "
                    f"{state.token_symbol} | m5×12: ${m5_rate:,.0f} | "
                    f"Threshold: ${threshold:,.0f}"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.stall_sell_pct,
                    reason=f"Stall — m5×12 ${m5_rate:,.0f} both below ${threshold:,.0f}"
                )
                state.stall_exit_done = True
                self.stall_exits += 1
            return  # End MC path

        # ═══════════════════════════════════════════════════════════════
        # DIP BUY POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════
        if state.strategy == "dip_buy":
            # Re-entrancy guard (2026-06-12 audit #1): the MC and Standard
            # branches early-return when a realtime exit is mid-flight; the
            # dip branch never did — so while _do_realtime_stop awaited its
            # Jupiter re-fetch, the 5s poll could fire a SECOND full close
            # (often a different reason: slow-bleed fired 4x in 15min on one
            # position). One gate for all four realtime-dedup sets.
            if (token_address in self._stop_triggered
                    or token_address in getattr(self, "_trail_triggered", ())
                    or token_address in getattr(self, "_post_tp1_trail_triggered", ())
                    or token_address in getattr(self, "_tp_triggered", ())):
                return

            # ── SHADOW: chart-reader signal-flip detector ────────────
            # Re-runs chart_reader periodically (60s cadence). Logs but does
            # NOT execute exits — paired-trade validation pending. Trails were
            # dropped 2026-05-01 because they fired at peak collapse on the
            # 0-of-133 set; this detector aims to fire on confluence (CHoCH
            # down + sweep failure + breakdown) BEFORE peak, so the verdict
            # arrives early enough to act on. We compare it to actual outcomes
            # before enforcing anything.
            try:
                await self._maybe_check_chart_signal_flip(token_address, state, pnl_pct)
                await self._maybe_check_1s_cascade(state, pnl_pct)
            except Exception as _e:
                logger.debug(f"[PositionManager] signal-flip check error for {state.token_symbol}: {_e}")

            # ── TIME-STOP SHADOW (paper-derived 2026-05-12) ──────────
            # Common pattern in open-source memecoin scalper bots (e.g.,
            # Swiper default = 5min hard stop). Pre-TP1 only — when age
            # crosses thresholds AND we're red, log what we'd save by
            # exiting on the clock. SHADOW only; no behavior change.
            #
            # Mining: validates whether time-based exit catches losses
            # earlier than condition-based exits. 87% of recent losers
            # peaked <+5% — many would benefit from a clock-stop.
            _ts_age = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
            if (not state.tp1_hit and pnl_pct < 0
                    and pnl_pct > -self.dip_stop_pct):
                for _bucket in (600, 1200, 1800):
                    # 30s window catches the bucket exactly once per position
                    # (loop runs every ~5-15s, so ≤1 hit per bucket per token).
                    if _bucket <= _ts_age < _bucket + 30:
                        logger.info(
                            f"[PositionManager/{self.chain_name}] "
                            f"time_stop_{_bucket}s SHADOW would-exit: "
                            f"{state.token_symbol} hold={_ts_age:.0f}s "
                            f"pnl={pnl_pct:+.2f}% (no behavior change)"
                        )
                        break

            # ── FAST-DUD EXIT — ENFORCED 2026-05-11 ───────────────────
            # Tighter stop applied to positions open >=60s that have
            # NEVER crossed +1.0% peak AND are currently at -1.5% or
            # worse. Pre-TP1 only.
            #
            # Validation (peak/mdd telemetry replayed on historical trades):
            #   Past 7d n=405: 83 fires, 0 harmed, Δ=+$164
            #   Past 5d n=177: 38 fires, 0 harmed, Δ=+$94
            #   Held-out n=23: 3 fires, 0 harmed, Δ=+$7.84
            #   Lifetime n=1011: 165 fires, 4 harmed ($1.52 total),
            #     save=$915. Harm/save ratio 0.17% (16:1 save/harm).
            #
            # Modern-subset proxy validation (peak<1% + pnl<0 + hold>60s,
            # n=123/684 over 9 days): avg current loss -$2.30, avg if
            # exited at -1.5% = -$0.30. Save: +$245.51 over 9 days
            # = ~$27/day. ~14 fires/day historical, ~3-4/day post-RSI-gate.
            #
            # Reads peak from the trader Position object since
            # PositionState doesn't track peak_pnl_pct directly.
            age_s = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
            _tp_for_peak = self.open_positions_ref.get(token_address)
            _dud_peak = (
                getattr(_tp_for_peak, "peak_pnl_pct", 0.0) or 0.0
            ) if _tp_for_peak else 0.0
            # Tightened 2026-05-12 after RKC false-positive: 63s hold,
            # peak=0%, exit at -3.68%, then token recovered +8% within hours.
            # Original 60s + -1.5% threshold was firing on normal early-entry
            # chop. New conditions:
            #   - min hold: 60s -> 180s (gives rebound 3min before cutting)
            #   - pnl floor: -1.5% -> -2.5% (deeper before trigger)
            #   - peak < 1.0% unchanged (still never-green-after-180s)
            # 2026-05-17 PM — fast_dud RETIRED. Was: exit at -2.5% if peak<+1%
            # after 180s hold. Cut bot off from pumps that developed after the
            # 3min window. User-flagged: bot just missed a pump because fast_dud
            # exited mid-development. Now bot holds losers to -4% hard stop or
            # soft trail catches a recovery. Pre-stop bail-out (commit 59d0296)
            # still catches genuinely dying volume.
            # if (not state.tp1_hit
            #         and age_s >= 180
            #         and _dud_peak < 1.0
            #         and pnl_pct <= -2.5
            #         and pnl_pct > -self.dip_stop_pct
            #         and state.fast_dud_first_ts is None):
            #     [fast_dud exit removed]
            #     return

            # ── VOLUME DEATH EXIT ────────────────────────────────────
            # Close losing positions whose liquidity has structurally died.
            # Guards: only fires when we're already down ≥3% AND 30min+ into
            # the hold — so active BULL-class chop can't trip it (winners and
            # early positions are protected by pnl_pct > -3 condition).
            if age_s >= 1800 and pnl_pct <= -3.0:
                snapshot = await self._fetch_volume_snapshot(token_address)
                if snapshot is not None:
                    v_h24, v_h1, v_m5 = snapshot
                    decay_threshold = v_h24 / 48.0 if v_h24 > 0 else 0
                    # 2026-05-17 PRE-STOP BAIL-OUT — new leg.
                    # Forensics on 134 closed trades: avg realized loss was -$1.23
                    # on a $20 position = effective -6% despite -4% nominal stop.
                    # The 2pp gap is bad-fill slippage on dying liquidity. Bail at
                    # -2% if volume is clearly dying (v_m5 < $500 AND v_h1 below
                    # 1.5x decay-floor) — captures the trade BEFORE the stop fills
                    # at -6%. Saves ~$0.50/trade × ~16% stop-rate ≈ $0.08/trade.
                    _bail_dying = (
                        v_m5 < 500.0
                        and v_h1 < decay_threshold * 1.5
                        and pnl_pct <= -2.0
                        and not state.tp1_hit
                        and state.strategy == "dip_buy"
                    )
                    if v_m5 == 0 and v_h1 < decay_threshold:
                        logger.warning(
                            f"[PositionManager/{self.chain_name}] 💀 VOLUME DEATH: "
                            f"{state.token_symbol} pnl={pnl_pct:.1f}% "
                            f"vol_m5=$0 vol_h1=${v_h1/1e3:.1f}k (<${decay_threshold/1e3:.1f}k) "
                            f"— closing"
                        )
                        await self._execute_sell(
                            token_address, state,
                            pct=1.0,
                            reason=(
                                f"Volume death exit (pnl={pnl_pct:.1f}%, "
                                f"vol_m5=0, vol_h1=${v_h1/1e3:.1f}k)"
                            ),
                        )
                        if self.scanner:
                            self.scanner.register_stop_loss(
                                token_address, state.token_symbol,
                                state.current_price, cooldown_seconds=7200
                            )
                        return
                    # 2026-05-17 PRE-STOP BAIL-OUT — milder dying-volume branch.
                    if _bail_dying:
                        logger.warning(
                            f"[PositionManager/{self.chain_name}] 🩹 PRE-STOP BAIL-OUT: "
                            f"{state.token_symbol} pnl={pnl_pct:.2f}% "
                            f"vol_m5=${v_m5:.0f}<500 vol_h1=${v_h1/1e3:.1f}k<{decay_threshold*1.5/1e3:.1f}k "
                            f"— closing before -4% stop slips to -6%"
                        )
                        await self._execute_sell(
                            token_address, state,
                            pct=1.0,
                            reason=(
                                f"Pre-stop bail-out (pnl={pnl_pct:.2f}%, "
                                f"vol_m5=${v_m5:.0f}, vol_h1=${v_h1/1e3:.1f}k)"
                            ),
                        )
                        if self.scanner:
                            self.scanner.register_stop_loss(
                                token_address, state.token_symbol,
                                state.current_price, cooldown_seconds=7200
                            )
                        return

            # NOTE: age_s already computed above in fast-dud block.

            # ── DIP STALE PEAK EXIT — added 2026-05-09 ────────────────
            # Pre-TP1 fade detector. Catches positions that touched a real
            # peak (>=+5%) but failed to make a new high for 15+ minutes
            # AND have now retreated all the way back to break-even or red.
            # Different from a fixed trail: real TP1-bound runners reset
            # mins_since_peak to 0 every time they make a new high, so
            # this can't fire on them.
            #
            # Validation on n=53W + 58L recent closed: 0 winners cut, 5
            # losers caught early (avg ~$1 saved per fire). Why no winner
            # cut: a token doing +5% -> +6% -> +7% -> +8% TP1 keeps
            # resetting peak; mins_since_peak never reaches 15. A pre-TP1
            # fader (peak +5% then drift back to BE over 30+ min) holds
            # peak constant while the timer accumulates.
            #
            # Only fires pre-TP1. After TP1 the breakeven / winner-trail
            # / TP2 cascade owns the position.
            _peak_pnl_pct = getattr(state, 'peak_pnl_pct', 0.0) or 0.0
            _peak_at_secs = getattr(state, 'peak_pnl_at_secs', 0) or 0
            _mins_since_peak = (age_s - _peak_at_secs) / 60.0 if _peak_at_secs > 0 else 0

            # ── PEAK-GIVEBACK RESCUE — ENFORCED 2026-05-12 ────────────
            # Pre-TP1 rescue for the +1.5% to +5% "slow drift" dead zone
            # that fast_dud (peak<1%) and stale_peak (peak>=5%) both miss.
            # When peak >= +1.5% AND current pnl has given back >=50% of
            # peak, exit at market to lock in remaining gain.
            #
            # Reference case: CLUDE 2026-05-12 17:26 — peak +1.99%, drifted
            # to -10.55% stop over 30 min. With rescue: would have exited
            # near +1.0% = ~+$0.20 net instead of -$2.11.
            #
            # Validation (3d held-out 5/9-5/12, TRAIN n=74 / VAL n=33):
            #   TRAIN: 14 fires saving $+37.78
            #   VAL:   5 fires saving $+10.26
            # Targets the loser bucket peaking +1.5-5% then bleeding back
            # (28% of recent losses on VAL by count).
            #
            # Doesn't conflict with stale_peak (peak >= 5%) since gate is
            # peak < 5.0 strict. Doesn't conflict with TP1 ladder since
            # tp1_hit gate. Loss-cooldown only if exit is net-negative.
            if (not state.tp1_hit
                    and 1.5 <= _peak_pnl_pct < 5.0
                    and age_s >= 60
                    and pnl_pct <= _peak_pnl_pct * 0.5
                    and pnl_pct > -self.dip_stop_pct):
                logger.warning(
                    f"[PositionManager/{self.chain_name}] 🛟 PEAK-GIVEBACK: "
                    f"{state.token_symbol} peak=+{_peak_pnl_pct:.1f}% "
                    f"pnl={pnl_pct:+.1f}% "
                    f"(gave back >=50% of peak — exiting)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=(
                        f"Peak giveback exit (peak +{_peak_pnl_pct:.1f}%, "
                        f"pnl {pnl_pct:+.1f}%)"
                    ),
                )
                if pnl_pct < 0 and self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol,
                        state.current_price, cooldown_seconds=3600
                    )
                return

            if (not state.tp1_hit
                    and _peak_pnl_pct >= 5.0
                    and _mins_since_peak >= 15.0
                    and pnl_pct <= 0.0
                    and pnl_pct > -self.dip_stop_pct):
                logger.warning(
                    f"[PositionManager/{self.chain_name}] 🪦 DIP STALE PEAK: "
                    f"{state.token_symbol} peak=+{_peak_pnl_pct:.1f}% "
                    f"mins_since_peak={_mins_since_peak:.0f} pnl={pnl_pct:+.1f}% "
                    f"(pre-TP1 fade — closing at small loss)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=(
                        f"Dip stale-peak exit (peak +{_peak_pnl_pct:.1f}% "
                        f"{_mins_since_peak:.0f}m ago, pnl {pnl_pct:+.1f}%)"
                    )
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol,
                        state.current_price, cooldown_seconds=7200
                    )
                return

            # ── DIP SLOW BLEED EXIT — added 2026-05-08 ────────────────
            # After 60min the position has had time to work. If it's still
            # >=5% red, the bot was holding through a slow bleed waiting for
            # a recovery that statistically isn't coming — across today's
            # 26 long-hold trades (>=2hr) net was -$45.81 vs +$3.05 for
            # short holds (<2hr). Pattern was particularly stark on GMAR
            # x6 (held 2-7h each, all stopped at -12%).
            # Close at the smaller -5% wound instead of waiting for -12%.
            if (age_s >= 3600 and pnl_pct <= -5.0
                    and pnl_pct > -self.dip_stop_pct):
                logger.warning(
                    f"[PositionManager/{self.chain_name}] 🩸 DIP SLOW BLEED: "
                    f"{state.token_symbol} hold={age_s/60:.0f}min pnl={pnl_pct:.1f}% "
                    f"(closing early instead of waiting for -{self.dip_stop_pct:.0f}% stop)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"Dip slow-bleed exit (hold {age_s/60:.0f}min, pnl {pnl_pct:.1f}%)"
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol,
                        state.current_price, cooldown_seconds=7200
                    )
                return

            # ── DIP STOP LOSS ─────────────────────────────────────────
            if pnl_pct <= -self.dip_stop_pct:
                if self._stop_grace_active(state, pnl_pct, age_s):
                    logger.info(
                        f"[PositionManager/{self.chain_name}] ⏳ STOP GRACE: "
                        f"{state.token_symbol} at {pnl_pct:.1f}% "
                        f"(age {age_s/60:.0f}min) — dip stop deferred "
                        f"(smart_follow A/B treatment)"
                    )
                    return
                logger.warning(
                    f"[PositionManager/{self.chain_name}] 🛑 DIP STOP: "
                    f"{state.token_symbol} at {pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"Dip stop -{self.dip_stop_pct:.0f}%"
                )
                self.stop_loss_hits += 1
                return

            # ── DIP SMART BEAR-FLIP EXIT — ENFORCED 2026-05-08 ────────
            # Promoted from SHADOW after phantom forward-test (n=1507 held-out)
            # showed +0.74%/trade lift over the 3.5% trail on the same
            # post-TP1 exits (29 better / 18 worse / 185 ties out of 232).
            # Earlier validation:
            #   chart-data sim TRAIN/TEST holdout (n=15665):
            #     +0.602%/trade train, +0.819%/trade test
            #   real-trade matched replay (n=221, post-2026-05-04):
            #     +0.060%/trade lift with re-tuned params (44 better, 21 worse)
            #
            # Mechanism: after TP1 (sells 50% in ladder mode), watch for
            # "position green but trend reversing." Fires when position
            # pnl > +3.0% AND last 3 1m bars green AND current 1m closed
            # RED with body > 0.3%. Captures the "winner-turning-loser"
            # pattern before the slower 3.5% trail converts the winner.
            #
            # Coexists with the existing 3.5% trail block below — whichever
            # fires first wins. Trail acts as safety net for cases where
            # the bear-flip price-action pattern doesn't trigger.
            if (state.tp1_hit and not state.tp2_hit
                    and pnl_pct > 3.0
                    and state.entry_price > 0):
                try:
                    from feeds.chart_data import assemble_chart_data
                    cd = await assemble_chart_data(
                        self.gt_client, state.pair_address,
                        dexs_client=self.dexs_client,
                    )
                    bars_1m = cd.candles_1m if cd and cd.candles_1m else []
                except Exception:
                    bars_1m = []
                if len(bars_1m) >= 4:
                    cur = bars_1m[-1]
                    prior_3 = bars_1m[-4:-1]
                    prior_all_green = all(
                        b.close > b.open for b in prior_3 if b.open > 0
                    )
                    if (prior_all_green and cur.close < cur.open
                            and cur.open > 0):
                        cur_body_pct = abs(cur.close - cur.open) / cur.open * 100
                        if cur_body_pct > 0.3:
                            logger.info(
                                f"[PositionManager/{self.chain_name}] "
                                f"🔄 DIP SMART BEAR-FLIP: "
                                f"{state.token_symbol} pnl=+{pnl_pct:.2f}% "
                                f"red_body={cur_body_pct:.2f}% "
                                f"(3 prior green, locking remainder)"
                            )
                            await self._execute_sell(
                                token_address, state,
                                pct=1.0,
                                reason=(
                                    f"Dip smart_bearflip exit "
                                    f"(pnl=+{pnl_pct:.2f}%, "
                                    f"red_body={cur_body_pct:.2f}%)"
                                ),
                            )
                            if self.scanner:
                                self.scanner.register_stop_loss(
                                    token_address, state.token_symbol,
                                    state.current_price,
                                    cooldown_seconds=3600,
                                )
                            return

            # ── DIP TRAIL EXHAUSTION EXITS — ENFORCED 2026-05-21 ───────
            # Two post-TP1 exit signals mined from 43 recent winners
            # (.exh_top_v2.json, 5m GT bars, 14d cohort):
            #
            #  Rule A — VOL DRYING: last 1m bar volume < 30% of prior
            #    4-min average. Detection 19% of winners, +9.81% avg
            #    lift over actual exit, $1.01/trade (3/4 better/0 worse).
            #
            #  Rule B — WICK REJECTION on last complete 5m bar:
            #    upper_wick >= 2x body AND close in lower 40% of bar
            #    range. Detection 21%, +3.85% avg lift, $0.18/trade
            #    (9/9 better, 0 worse — 100% positive in mining).
            #
            # Both gated on tp1_hit (75% already locked at TP1) + pnl>=
            # 5% (post-TP1 zone). The bar-completion requirements
            # (4 prior 1m bars / 1 complete 5m bar) provide natural
            # noise buffer post-TP1.
            #
            # User-validated 2026-05-21: UFO #1 (3-push + wick rejection
            # at +7.1%) and UFO #2 (vol_m5=0 at +6.5%) — both manual
            # exits captured the topping signature before the standard
            # 3pp peak-trail would have fired.
            if (state.tp1_hit and not state.tp3_hit
                    and pnl_pct >= 5.0
                    and state.entry_price > 0):
                try:
                    from feeds.chart_data import assemble_chart_data
                    cd_ex = await assemble_chart_data(
                        self.gt_client, state.pair_address,
                        dexs_client=self.dexs_client,
                    )
                    bars_1m_ex = cd_ex.candles_1m if cd_ex and cd_ex.candles_1m else []
                    bars_5m_ex = cd_ex.candles_5m if cd_ex and cd_ex.candles_5m else []
                except Exception:
                    bars_1m_ex = []
                    bars_5m_ex = []

                # Rule A — VOL DRYING.
                if len(bars_1m_ex) >= 5:
                    _recent_v = float(bars_1m_ex[-1].volume or 0)
                    _prior_vs = [float(b.volume or 0) for b in bars_1m_ex[-5:-1]]
                    _prior_avg_v = sum(_prior_vs) / len(_prior_vs) if _prior_vs else 0.0
                    if _prior_avg_v > 0 and _recent_v < _prior_avg_v * 0.3:
                        logger.info(
                            f"[PositionManager/{self.chain_name}] "
                            f"💨 DIP VOL DRYING EXIT: "
                            f"{state.token_symbol} pnl=+{pnl_pct:.2f}% "
                            f"recent_1m_vol={_recent_v:.0f}<"
                            f"{_prior_avg_v*0.3:.0f} (prior_4m_avg={_prior_avg_v:.0f})"
                        )
                        await self._execute_sell(
                            token_address, state,
                            pct=1.0,
                            reason=(
                                f"Dip vol_drying exit (pnl=+{pnl_pct:.2f}%, "
                                f"recent_1m_vol={_recent_v:.0f}, "
                                f"prior_4m_avg={_prior_avg_v:.0f})"
                            ),
                        )
                        if self.scanner:
                            self.scanner.register_stop_loss(
                                token_address, state.token_symbol,
                                state.current_price,
                                cooldown_seconds=3600,
                            )
                        return

                # Rule B — WICK REJECTION on last complete 5m candle.
                # bars_5m_ex[-1] may be in-progress; use [-2] for the
                # most-recently-CLOSED 5m bar.
                if len(bars_5m_ex) >= 2:
                    _last_5m = bars_5m_ex[-2]
                    _o, _h, _l, _c = (
                        float(_last_5m.open or 0),
                        float(_last_5m.high or 0),
                        float(_last_5m.low or 0),
                        float(_last_5m.close or 0),
                    )
                    if _h > _l and _o > 0:
                        _body = abs(_c - _o)
                        _upper_wick = _h - max(_o, _c)
                        _rng = _h - _l
                        _lower_pos = (_c - _l) / _rng if _rng > 0 else 1.0
                        _rng_pct = _rng / _l if _l > 0 else 0
                        # Require: wick≥2x body, close in lower 40%,
                        # bar range ≥0.5% (filters tiny noise bars).
                        if (_upper_wick > 0
                                and _upper_wick >= _body * 2.0
                                and _lower_pos < 0.4
                                and _rng_pct > 0.005):
                            _ratio = _upper_wick / max(_body, 1e-12)
                            logger.info(
                                f"[PositionManager/{self.chain_name}] "
                                f"🪝 DIP WICK REJECTION EXIT: "
                                f"{state.token_symbol} pnl=+{pnl_pct:.2f}% "
                                f"wick:body={_ratio:.1f}x "
                                f"close_pos={_lower_pos:.2f} (lower 40%)"
                            )
                            await self._execute_sell(
                                token_address, state,
                                pct=1.0,
                                reason=(
                                    f"Dip wick_rejection exit "
                                    f"(pnl=+{pnl_pct:.2f}%, "
                                    f"wick:body={_ratio:.1f}x, "
                                    f"close_pos={_lower_pos:.2f})"
                                ),
                            )
                            if self.scanner:
                                self.scanner.register_stop_loss(
                                    token_address, state.token_symbol,
                                    state.current_price,
                                    cooldown_seconds=3600,
                                )
                            return

            # ── DIP PRE-TP1 LOCK-IN TRAIL — MOVED TO REALTIME 2026-05-15 ──
            # The single-tick trigger version of this rule fired too eagerly
            # once the PoolPriceFeed went live (RAGEGUY/fish/FAHHHH 05-15 all
            # exited at +0.5-0.9% then immediately ran to +5-21%). Replaced
            # by check_exhaustion_realtime which uses Option B: 60s
            # continuous-below-threshold confirmation + hard guard at -2%.
            #
            # Historical motivation preserved for context: DISCLOSURE peak
            # +3.7% → -8.1% and CHINA peak +3.1% → -8.3% (both pre-trail).
            # New realtime path still protects these (price drops continuously
            # past the confirmation window in those cases).
            pass

            # ── DIP POST-TP1 TRAIL — REMOVED 2026-05-16 PM ────────────
            # Was firing on first 1pp drop from peak with NO confirmation —
            # clipped the +3-5% peak cohort at noise wiggles. The realtime
            # path (check_post_tp1_trail_realtime) now covers all peak tiers
            # >= +3% with confirmation + recovery hysteresis (see Edit 1+2
            # at line ~2870). Two trail paths racing was the architecture
            # bug. Single source of truth: realtime only.
            #
            # Fallback if realtime tick stream breaks: _evaluate_stop_loss
            # still fires hard stop at -dip_stop_pct on this 5s cycle.

            # ── DIP TAKE PROFIT (3-tier ladder, check highest first) ───
            if pnl_pct >= self.dip_tp3_pct and not state.tp3_hit:
                state.tp3_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 DIP TP3: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.dip_tp3_sell,
                    reason=f"Dip TP3 +{pnl_pct:.1f}%"
                )
                return

            if pnl_pct >= self.dip_tp2_pct and not state.tp2_hit:
                state.tp2_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 DIP TP2: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.dip_tp2_sell,
                    reason=f"Dip TP2 +{pnl_pct:.1f}%"
                )
                return

            if pnl_pct >= self.dip_tp1_pct and not state.tp1_hit:
                state.tp1_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 DIP TP1: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=(state.tp1_sell_override or self.dip_tp1_sell),
                    reason=f"Dip TP1 +{pnl_pct:.1f}%"
                )
                return

            return  # End dip_buy path

        # ═══════════════════════════════════════════════════════════════
        # SCALP POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════
        if state.strategy == "scalp":
            await self._evaluate_scalp(token_address, state)
            return

        # ═══════════════════════════════════════════════════════════════
        # STANDARD POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════

        # ── WINNER PROTECTION — trail from peak once up ≥10% (pre or post TP1)
        # Fires pre-TP1 too: a token that peaks at +30% then crashes back deserves
        # an exit near the peak, not at the breakeven floor. Minimum 10% peak gain
        # avoids triggering on normal entry-level volatility.
        _MIN_PEAK_FOR_TRAIL = 10.0
        _peak_gain_pct_std = (state.peak_price - state.entry_price) / state.entry_price * 100 if state.entry_price > 0 else 0
        if (state.peak_price > 0
                and _peak_gain_pct_std >= _MIN_PEAK_FOR_TRAIL
                and state.current_price <= state.peak_price * (1 - self.winner_trail_pct / 100)):
            drop_from_peak = (state.peak_price - state.current_price) / state.peak_price * 100
            logger.info(
                f"[PositionManager/{self.chain_name}] 🔒 WINNER TRAIL: "
                f"{state.token_symbol} -{drop_from_peak:.1f}% from peak "
                f"(peaked at +{_peak_gain_pct_std:.1f}%)"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Winner trail -{drop_from_peak:.1f}% from peak"
            )
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=14400  # 4h — token already ran, don't re-enter
                )
            return

        # ── BREAKEVEN LOCK — once up 8%, protect at +3% ─────────────────
        # Trigger raised from 2%→8%: a 2% move is noise on volatile tokens,
        # not a real gain worth protecting. Floor raised from 0%→+3%: ensures
        # the exit actually covers slippage and nets a small profit.
        # Briefly lowered to 5% (commit 7119e67) but reverted: with only 12
        # records of post-Batch-2 trajectory data we can't tell if drawdowns
        # happened before or after crossing +5%, so a 5% trigger may have
        # killed TP1-bound runners that briefly dipped past their +5% peak.
        # Re-evaluate after ~30 more trades have peak/drawdown data.
        _BREAKEVEN_TRIGGER = 8.0   # lock after a real move
        _BREAKEVEN_FLOOR   = 3.0   # exit at +3% (covers slippage, nets small gain)
        if not state.breakeven_locked and pnl_pct >= _BREAKEVEN_TRIGGER:
            state.breakeven_locked = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🔒 BREAKEVEN LOCKED: "
                f"{state.token_symbol} at +{pnl_pct:.1f}% — "
                f"stop raised to +{_BREAKEVEN_FLOOR:.0f}%"
            )

        if state.breakeven_locked and not state.tp1_hit and pnl_pct <= _BREAKEVEN_FLOOR:
            logger.info(
                f"[PositionManager/{self.chain_name}] 🔒 BREAKEVEN EXIT: "
                f"{state.token_symbol} at {pnl_pct:+.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Breakeven exit {pnl_pct:+.1f}%"
            )
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=7200  # 2h cooldown for breakeven exit
                )
            return

        # ── BREAKEVEN AFTER SCALP — if scalp has fired and price returns to entry ──
        if self.scalper is not None:
            scalp_state = self.scalper._states.get(token_address)
            if (scalp_state is not None and
                    (scalp_state.completed_cycles or scalp_state.active_cycle is not None) and
                    state.current_price <= state.entry_price):
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🔁 BREAKEVEN-AFTER-SCALP: "
                    f"{state.token_symbol} back at entry after scalp fired — closing 100%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason="Breakeven after scalp"
                )
                return

        # ── STOP LOSS — Hard stop with flash crash detection ──────────────
        # Skip if realtime feed already claimed this position — prevents
        # a duplicate polling-loop sell racing against the realtime ensure_future.
        if token_address in self._stop_triggered:
            return
        if pnl_pct <= -self.stop_loss_pct:
            age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
            is_flash_crash = age_seconds <= 120  # stop-loss in ≤2 minutes = likely rug
            logger.warning(
                f"[PositionManager/{self.chain_name}] 🛑 STOP LOSS: "
                f"{state.token_symbol} at {pnl_pct:.1f}%"
                + (" ⚠️ FLASH CRASH — possible rug" if is_flash_crash else "")
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Stop loss -{self.stop_loss_pct}%"
            )
            self.stop_loss_hits += 1
            cooldown = 86400 if is_flash_crash else 14400  # 24h on rug, 4h on normal stop
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=cooldown
                )
            return

        # ── EARLY MOMENTUM FAILURE EXIT — graduated time/threshold tiers ────
        # Winners pop fast (1-4 min) or grind positively. Losers drop early and sit.
        # Tier 1 — 3 min: fast dumps (rugs, coordinated sells) drop hard immediately
        # Tier 2 — 15 min: medium dumps that haven't recovered (extended from 5min)
        # Pyramids get tighter exits (bought at the top, higher reversal risk)
        if state.strategy != "dip_buy":
            _is_pyramid = "[PYRAMID]" in state.token_symbol
            _early_exit_reason = None
            if not state.tp1_hit:
                if age_seconds >= 1800 and pnl_pct <= -5.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — no momentum at 30min"
                elif age_seconds >= 180 and pnl_pct <= -8.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — fast dump at 3min"
                # Pyramids: tighter — bought at the top, exit sooner if reversing
                elif _is_pyramid and age_seconds >= 420 and pnl_pct <= -3.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — pyramid no momentum at 7min"
                elif _is_pyramid and age_seconds >= 180 and pnl_pct <= -5.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — pyramid fast dump at 3min"

            if _early_exit_reason:
                logger.info(
                    f"[PositionManager/{self.chain_name}] ⏱ EARLY EXIT: "
                    f"{state.token_symbol} {pnl_pct:+.1f}% at {age_seconds/60:.1f}min — {_early_exit_reason}"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=_early_exit_reason,
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=7200  # 2h cooldown — bad entry, not rug
                    )
                return

        # ── TXN RATE COLLAPSE — leading indicator: momentum dead before price ───
        # If h1 txn rate falls to <10% of entry baseline, the market has walked
        # away. Exit proactively rather than waiting for the price to confirm.
        # Require ≥50 entry txns (thin tokens have naturally low, noisy counts).
        # 5-minute stabilization window — h1 window rolls on fresh entries.
        if (not state.tp1_hit
                and state.entry_txns_h1 >= 50
                and state.current_txns_h1 > 0
                and state.current_txns_h1 < state.entry_txns_h1 * 0.10
                and age_seconds >= 300):
            logger.info(
                f"[PositionManager/{self.chain_name}] 📉 TXN COLLAPSE: "
                f"{state.token_symbol} — {state.current_txns_h1} txns/hr "
                f"vs {state.entry_txns_h1} at entry "
                f"({state.current_txns_h1 / state.entry_txns_h1 * 100:.0f}% of baseline)"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Txn collapse — {state.current_txns_h1}/hr vs {state.entry_txns_h1}/hr"
            )
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=3600
                )
            return

        # ── TAKE PROFIT TIERS ────────────────────────────────────────────
        if pnl_pct >= self.tp3_pct and not state.tp3_hit:
            state.tp3_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 TP3: "
                f"{state.token_symbol} +{pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.tp3_sell,
                reason=f"TP3 +{pnl_pct:.1f}%"
            )
            self.tp3_hits += 1
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=14400  # 4h — token already ran, don't re-enter
                )
            return

        if pnl_pct >= self.tp2_pct and not state.tp2_hit:
            state.tp2_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 TP2: "
                f"{state.token_symbol} +{pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.tp2_sell,
                reason=f"TP2 +{pnl_pct:.1f}%"
            )
            self.tp2_hits += 1
            return

        if pnl_pct >= self.tp1_pct and not state.tp1_hit:
            state.tp1_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 TP1: "
                f"{state.token_symbol} +{pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.tp1_sell,
                reason=f"TP1 +{pnl_pct:.1f}%"
            )
            self.tp1_hits += 1

            # Pyramids disabled — 18% win rate, structurally buys at the top.
            return

        # ── STALL DETECTION ──────────────────────────────────────────────
        if (not state.stall_exit_done and
                state.hours_open >= self.stall_min_hours and
                state.is_stalled):
            m5_rate = state.current_m5_volume * 12
            threshold = state.entry_volume_usd * state.stall_threshold
            tier = (
                "High entry" if state.entry_volume_usd >= 100_000
                else "Medium entry" if state.entry_volume_usd >= 20_000
                else "Low entry"
            )
            logger.info(
                f"[PositionManager/{self.chain_name}] 😴 STALL: "
                f"{state.token_symbol} | "
                f"m5×12: ${m5_rate:,.0f} | "
                f"h1: ${state.current_h1_volume:,.0f} | "
                f"Threshold: ${threshold:,.0f} ({state.stall_threshold*100:.0f}%) | "
                f"Tier: {tier}"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.stall_sell_pct,
                reason=(
                    f"Stall — m5×12 ${m5_rate:,.0f} + "
                    f"h1 ${state.current_h1_volume:,.0f} "
                    f"both below ${threshold:,.0f}"
                )
            )
            state.stall_exit_done = True
            self.stall_exits += 1
            await self.telegram.send(
                f"😴 *Stall Exit* [{self.chain_name}]\n\n"
                f"🪙 ${state.token_symbol}\n"
                f"📊 PnL: {pnl_pct:+.1f}%\n"
                f"📉 m5 run-rate: ${m5_rate:,.0f}/hr\n"
                f"📉 h1 volume: ${state.current_h1_volume:,.0f}/hr\n"
                f"🎯 Threshold: ${threshold:,.0f}/hr "
                f"({state.stall_threshold*100:.0f}% — {tier})\n"
                f"✅ Sold {self.stall_sell_pct*100:.0f}%\n"
                f"⏱ Open: {state.hours_open:.1f}h"
            )
            return

        # ── AVERAGE DOWN — DISABLED ──────────────────────────────────────
        # Disabled: adds capital to losers right before early exit tiers cut them.
        # A position at -2.7% that gets averaged doubles exposure into the 5min/-5%
        # check. Works directly against the graduated exit strategy.
        # Re-enable via config flag if strategy changes.

    async def external_exit(self, token_address: str, reason: str) -> bool:
        """Strategy-initiated full exit (2026-06-10, smart_follow elite-exit
        mirroring): close an open position at market with an auditable reason.
        Returns False if no open state for the token. Paper-safe: routes through
        the same _execute_sell as every other exit."""
        addr = (token_address or "").lower()
        state = self._states.get(addr)
        if not state:
            return False
        logger.info(
            f"[PositionManager/{self.chain_name}] 🚪 EXTERNAL EXIT: "
            f"{state.token_symbol} — {reason}"
        )
        await self._execute_sell(addr, state, pct=1.0, reason=reason)
        return True

    def _stop_grace_active(self, state, pnl_pct: float, age_seconds: float) -> bool:
        """smart_follow stop-grace A/B: True while a treatment-arm position is
        inside its grace window — the hard stop is DEFERRED (not cancelled; it
        fires on the first check after the window) unless the catastrophic
        floor is breached, which always stops."""
        if not getattr(state, "stop_grace", False):
            return False
        if age_seconds >= _grace_minutes() * 60:
            return False
        if pnl_pct <= -_grace_floor_pct():
            return False
        return True

    def check_stop_loss_realtime(self, token_address: str, price_usd: float):
        """
        Called synchronously from the Axiom price feed on every price tick.
        Fires stop loss immediately via asyncio.ensure_future() rather than
        waiting up to 30 seconds for the poll cycle to notice the breach.
        """
        token_address = token_address.lower()
        state = self._states.get(token_address)
        if not state or state.entry_price <= 0:
            return
        if token_address in self._stop_triggered:
            return

        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        if age_seconds < 5:
            return  # Ignore first 5s — entry price settling

        # Sanity gates: reject single-tick price moves >20% in either direction.
        # 2026-04-27 BULL incident showed feed glitches causing 37x phantom
        # upticks (peak_price gets inflated → all trail/TP math becomes wrong)
        # and matching downward correction ticks (-97% in one tick → spurious
        # stop fires).  Real 20%+ moves typically span multiple ticks; a
        # single 20%+ tick is almost always a feed glitch.
        #
        # Cold-start fix (2026-05-03): when no prior realtime tick exists for
        # this token, compare against entry_price instead of skipping the gate.
        # The original `if last_rt > 0:` left the first tick after entry
        # ungated — a single glitched first read could lock in a phantom
        # stop (Goblin 02:53 baseline-mode: entry $1.030 → first tick
        # $0.063 → -94% phantom stop). Entry price is a stable, known
        # reference; legitimate first ticks won't deviate ±20% from it.
        last_rt = self._last_realtime_price.get(token_address, 0)
        ref_price = last_rt if last_rt > 0 else state.entry_price
        if not self._spike_should_accept(
                token_address, price_usd, ref_price, state.token_symbol):
            return
        self._last_realtime_price[token_address] = price_usd

        pnl_pct = (price_usd / state.entry_price - 1) * 100

        # Flash-crash gate: tighter -12% stop for first 90s on MC positions.
        # Rugs dump -25%+ in seconds; legitimate MC winners never dip -12% before
        # recovering (MARVIN +34% in 69s, FATCAT +25% in 5m — both move straight up).
        # After 90s revert to normal -25% MC stop for volatility tolerance.
        if state.is_micro_cap and age_seconds < 90:
            stop_pct = 12.0
        elif state.strategy == "graduation":
            stop_pct = 35.0
        elif state.strategy == "dip_buy":
            stop_pct = self.dip_stop_pct
        elif state.strategy == "scalp":
            stop_pct = self.scalp_stop_pct
        else:
            stop_pct = self.mc_stop_loss_pct if state.is_micro_cap else self.stop_loss_pct

        # Track min/peak even if no stop fires — needed for accurate
        # max_drawdown_pct and peak_pnl_pct on every sell. Without this,
        # the realtime path bypasses _update_price's min/peak tracking and
        # stops look like they had 0% drawdown on the trade record (a
        # surprising number of historical stops show max_dd=0 because of
        # this — the stop fired between poll cycles).
        if state.min_price_usd <= 0 or price_usd < state.min_price_usd:
            state.min_price_usd = price_usd
        if price_usd > state.peak_price:
            state.peak_price = price_usd
        # Sync to the persisted Position so trader.sell sees current values.
        _tp = self.open_positions_ref.get(token_address)
        if _tp is not None:
            _tp.current_price_usd = price_usd
            if state.min_price_usd > 0:
                _tp.min_price_usd = state.min_price_usd
            # Mirror _update_price's peak_pnl_pct tracking for stops that
            # fire between poll cycles (briefly-green-then-dumped trades).
            _prev_peak = getattr(_tp, "peak_pnl_pct", 0.0) or 0.0
            if pnl_pct > _prev_peak:
                _tp.peak_pnl_pct = pnl_pct
                _entry_mono = getattr(_tp, "entry_time_monotonic", 0) or 0
                _tp.peak_pnl_at_secs = (
                    int(time.monotonic() - _entry_mono) if _entry_mono > 0 else 0
                )

        if pnl_pct <= -stop_pct:
            if self._stop_grace_active(state, pnl_pct, age_seconds):
                if token_address not in self._grace_logged:
                    self._grace_logged.add(token_address)
                    logger.info(
                        f"[PositionManager/{self.chain_name}] ⏳ STOP GRACE: "
                        f"{state.token_symbol} at {pnl_pct:.1f}% "
                        f"(age {age_seconds/60:.0f}min) — realtime stop deferred "
                        f"(smart_follow A/B treatment)"
                    )
                return
            self._stop_triggered.add(token_address)
            state.current_price = price_usd
            label = (
                f"Grad stop loss -{stop_pct:.0f}% [realtime]"
                if state.strategy == "graduation" else
                f"Dip stop -{stop_pct:.0f}% [realtime]"
                if state.strategy == "dip_buy" else
                f"Scalp stop -{stop_pct:.1f}% [realtime]"
                if state.strategy == "scalp" else
                f"MC stop loss -{stop_pct:.0f}% [realtime]"
                if state.is_micro_cap else
                f"Stop loss -{stop_pct:.0f}% [realtime]"
            )
            logger.warning(
                f"[PositionManager/{self.chain_name}] ⚡ REALTIME STOP: "
                f"{state.token_symbol} at {pnl_pct:.1f}% — scheduling immediate sell"
            )
            # Flash crash detection: ≤ 120s hold = likely rug → 24h cooldown
            is_flash_crash = age_seconds <= 120
            cooldown = 86400 if is_flash_crash else 7200  # 24h rug, 2h normal realtime stop

            # Verify the trigger tick against a fresh Jupiter AMM quote before
            # executing. WS feeds occasionally emit junk ticks (one tick at
            # -16.6%, next back to -0%); without a confirmation step we sell
            # on noise. Require the fresh price to be at least 60% of the way
            # to the stop (e.g., -9% for a -15% stop) to confirm the breach.
            trigger_pnl_pct = pnl_pct
            trigger_stop_pct = stop_pct

            async def _do_realtime_stop(addr, st, lbl, trigger_price, cd_seconds):
                try:
                    fresh_price = await self._fetch_jupiter_price(addr)
                    if fresh_price > 0 and st.entry_price > 0:
                        fresh_pnl = (fresh_price / st.entry_price - 1) * 100
                        if fresh_pnl > -trigger_stop_pct * 0.6:
                            logger.warning(
                                f"[PositionManager/{self.chain_name}] ⚡ REALTIME STOP "
                                f"rejected for {st.token_symbol}: tick={trigger_pnl_pct:.1f}% "
                                f"but Jupiter spot={fresh_pnl:.1f}% — discarding as tick noise"
                            )
                            self._stop_triggered.discard(addr)
                            return
                    if self.scanner:
                        self.scanner.register_stop_loss(
                            addr, st.token_symbol, trigger_price,
                            cooldown_seconds=cd_seconds
                        )
                    await self._execute_sell(addr, st, pct=1.0, reason=lbl)
                    self.stop_loss_hits += 1
                    if st.strategy == "scalp" and self.scalp_queue:
                        pnl_usd = st.position_size_usd * st.pnl_pct / 100
                        self.scalp_queue.on_scalp_close(addr, "stop_loss", pnl_usd)
                except Exception as e:
                    logger.error(
                        f"[PositionManager/{self.chain_name}] ⚡ Realtime stop sell failed for "
                        f"{st.token_symbol}: {e} — clearing trigger for retry"
                    )
                    self._stop_triggered.discard(addr)

            asyncio.ensure_future(_do_realtime_stop(
                token_address, state, label, price_usd, cooldown
            ))

    def check_take_profit_realtime(self, token_address: str, price_usd: float):
        """
        Mirror of check_stop_loss_realtime for take-profit thresholds.
        Catches fast peaks the polled path misses — without this, a spike
        that touches TP1 between polls and reverses gets silently lost
        (SCAM 2026-05-02: dashboard showed +$1.60=+8% via Axiom WS, but
        polled path's max was +3.75%, so TP1=+8% would never have fired
        even with the lower threshold).

        Fires only for dip_buy currently — MC tiers and scalp have their
        own TP semantics that intentionally use the polled cadence.
        """
        token_address = token_address.lower()
        state = self._states.get(token_address)
        if not state or state.entry_price <= 0:
            return
        if state.strategy != "dip_buy":
            return
        if state.tp1_hit:
            return  # already taken profit
        if token_address in self._tp_triggered:
            return

        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        if age_seconds < 5:
            return

        # Same single-tick sanity gate the stop path uses (rejects 20%+
        # single-tick moves as feed noise). Cold-start fallback to
        # entry_price when no prior tick exists, matching stop path.
        last_rt = self._last_realtime_price.get(token_address, 0)
        ref_price = last_rt if last_rt > 0 else state.entry_price
        if ref_price > 0:
            if price_usd > ref_price * 1.20 or price_usd < ref_price * 0.80:
                return  # logged by stop path
        # don't update _last_realtime_price here — stop path owns it

        pnl_pct = (price_usd / state.entry_price - 1) * 100
        tp1_pct = self.dip_tp1_pct

        if pnl_pct < tp1_pct:
            return

        # Track peak even if TP doesn't fire after Jupiter verification —
        # observability for forward analysis.
        _tp = self.open_positions_ref.get(token_address)
        if _tp is not None:
            _prev_peak = getattr(_tp, "peak_pnl_pct", 0.0) or 0.0
            if pnl_pct > _prev_peak:
                _tp.peak_pnl_pct = pnl_pct
                _entry_mono = getattr(_tp, "entry_time_monotonic", 0) or 0
                if _entry_mono > 0:
                    _tp.peak_pnl_at_secs = int(time.monotonic() - _entry_mono)

        self._tp_triggered.add(token_address)
        logger.info(
            f"[PositionManager/{self.chain_name}] ⚡ REALTIME TP1: "
            f"{state.token_symbol} at +{pnl_pct:.1f}% — verifying via Jupiter"
        )

        async def _do_realtime_tp(addr, st, trigger_pnl):
            try:
                # Verify via Jupiter — same noise-rejection logic as stops.
                # Threshold loosened 2026-05-16 from 0.75x to 0.5x of tp1_pct
                # after Dust bug review: 5 trades in 7d (Dust, DISCLOSURE,
                # CHINA, PHANNY, BABYTROLL) peaked >=+3% but TP1 fire was
                # rejected by Jupiter check (returned spot below 2.25%) due
                # to 1-3s Jupiter API latency during fast retraces. With
                # TP1=3%, raising tolerance to <1.5% (Jupiter must be wildly
                # off pool feed to reject) catches the Dust class while
                # still rejecting true single-tick phantom spikes (those
                # would show Jupiter spot near 0%, well below 1.5%).
                # Combined cohort loss recovered: ~+$2.93 over 7d.
                fresh_price = await self._fetch_jupiter_price(addr)
                if fresh_price > 0 and st.entry_price > 0:
                    fresh_pnl = (fresh_price / st.entry_price - 1) * 100
                    if fresh_pnl < tp1_pct * 0.5:
                        logger.warning(
                            f"[PositionManager/{self.chain_name}] ⚡ REALTIME TP "
                            f"rejected for {st.token_symbol}: tick=+{trigger_pnl:.1f}% "
                            f"but Jupiter spot={fresh_pnl:.1f}% (need >={tp1_pct*0.5:.1f}%) — discarding as tick noise"
                        )
                        self._tp_triggered.discard(addr)
                        return
                st.tp1_hit = True
                # Sync to Position (persisted across restart)
                _pos = self.open_positions_ref.get(addr)
                if _pos is not None:
                    _pos.take_profit_1_hit = True
                await self._execute_sell(
                    addr, st,
                    pct=self.dip_tp1_sell,
                    reason=f"Dip TP1 +{trigger_pnl:.1f}% [realtime]"
                )
            except Exception as e:
                logger.error(
                    f"[PositionManager/{self.chain_name}] ⚡ Realtime TP sell failed for "
                    f"{st.token_symbol}: {e} — clearing trigger for retry"
                )
                self._tp_triggered.discard(addr)

        asyncio.ensure_future(_do_realtime_tp(token_address, state, pnl_pct))

    def check_exhaustion_realtime(self, token_address: str, price_usd: float):
        """
        Pre-TP1 exhaustion trail with confirmation. Called on every Helius
        pool tick (sub-second). Replaces the candle-based pre-TP1 trail that
        fired prematurely on RAGEGUY/fish/FAHHHH 2026-05-15 (all exited
        +0.5-0.9% then ran to +5-21%).

        Two-mode logic — see docs/superpowers/specs (Option A / hybrid):
          1. HARD GUARD: if peak >= +3% AND pnl <= -2%, fire immediately
             (catches DIRECTOR-style fast reversals before they hit -7%
             stop).
          2. SOFT TRAIL: if peak >= +3% AND pnl <= peak - 1.5pp:
              - Arm: set pending_exit_since_ts on first trigger
              - Confirm: if 60s elapse continuously below threshold, fire
              - Disarm: if pnl recovers to peak - 1.0pp, clear pending

        Sub-second reaction. The 60s confirmation window is continuous —
        every tick checks both the threshold and the elapsed clock. Memes
        oscillate sub-bar; the candle-based version saw single-tick wicks
        as exits. This version waits for sustained weakness.

        dip_buy only. Other strategies have their own trail/exit logic.
        """
        token_address = token_address.lower()
        state = self._states.get(token_address)
        if not state or state.entry_price <= 0:
            return
        if state.strategy != "dip_buy":
            return
        if state.tp1_hit:
            return  # post-TP1 trail handles this case
        if token_address in self._trail_triggered:
            return
        if token_address in self._stop_triggered:
            return  # stop is firing; let it run

        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        if age_seconds < 5:
            return  # let entry price settle

        # Sanity gate — same spike-rejection logic used by stop/TP paths.
        last_rt = self._last_realtime_price.get(token_address, 0)
        ref_price = last_rt if last_rt > 0 else state.entry_price
        if ref_price > 0:
            if price_usd > ref_price * 1.20 or price_usd < ref_price * 0.80:
                return  # single-tick glitch; stop path owns the rejection log
        # don't update _last_realtime_price here — stop path owns it

        # Update peak (peak_price is also updated by stop path's tracker
        # but we may run before that on a given tick — be defensive).
        if price_usd > state.peak_price:
            state.peak_price = price_usd

        pnl_pct = (price_usd / state.entry_price - 1) * 100
        peak_pct = (state.peak_price / state.entry_price - 1) * 100

        _MIN_PEAK = 2.5  # Lowered 3.0 → 2.5 on 2026-05-16. Soft-trail threshold.
        _DROP_PP = 1.5
        _CONFIRM_S = 60.0
        _RECOVERY_PP = 1.0  # drop tightens to this → disarm
        # PANIC exit — 2026-05-19. Catastrophic collapse override.
        # If drop from peak >= 6pp AND armed for 5s, fire immediately.
        # Catches "memecoins"-class case: peak +3.1%, exit -8.5% (11.6pp
        # give-back during the 60s confirm window). Lifetime backfill
        # showed only 1 such case (memecoins -$1.93) — narrow, surgical
        # change. Doesn't affect V-shape recoveries with drops <6pp
        # (PAC tonight at 4.5pp would not panic-fire).
        _PANIC_DROP_PP = 6.0
        _PANIC_CONFIRM_S = 5.0

        # 2026-05-18 — pre-TP1 HARD GUARD removed. Universe-recorder sim
        # (n=2691, conservative proxy: peak>=2.5 AND exit<=-2 fires) showed
        # the hard guard COST -1.49pp/trade vs runner-tilt baseline, -$801/day
        # at $20 size. Only 13.2% of fires would have hit -15% stop; the other
        # 87% recovered enough that the runner-tilt ladder caught them with
        # net-positive pnl (avg +3.05% without guard). Every threshold combo
        # tested (peak >= {2.5..10}, pnl <= {-2..-8}) was net-negative.
        # Fundamentally incompatible with runner-tilt thesis ("give tokens
        # room to move around"). Soft trail (60s confirm window) retained as
        # the only pre-TP1 exit path besides TP1 (+5%) and stop (-15%).

        # CARVE-OUT 2026-05-19: only fire pre-TP1 trail if the position
        # is actually underwater (pnl <= -2%) OR catastrophic drop (panic
        # 6pp+). Saves false trails on small-peak consolidations that
        # later recover to TP1 (AMERICA +7.4% post-exit, VIRL +67%
        # post-exit overnight). Lifetime: 17/18 historical pre-TP1 trail
        # fires were at pnl between -1.2% and +2.7% — false-bottom signals.
        # Only memecoins (-8.5% at fire) was the real catastrophic catch
        # and it survives the gate via _PANIC_DROP_PP escape hatch.
        # Slow-bleed exit + pre-stop bail-out are separate paths, unaffected.
        _PNL_FLOOR = -2.0

        # SOFT TRAIL with confirmation window
        if peak_pct >= _MIN_PEAK:
            drop_pp = peak_pct - pnl_pct
            _passes_pnl_gate = (
                pnl_pct <= _PNL_FLOOR or drop_pp >= _PANIC_DROP_PP
            )
            if drop_pp >= _DROP_PP and _passes_pnl_gate:
                # Below threshold. Arm if not yet armed.
                if state.pending_exit_since_ts is None:
                    state.pending_exit_since_ts = time.monotonic()
                    logger.info(
                        f"[PositionManager/{self.chain_name}] 🔒 PRE-TP1 ARMED: "
                        f"{state.token_symbol} peak +{peak_pct:.1f}% now {pnl_pct:+.1f}% "
                        f"(drop {drop_pp:.1f}pp) — {_CONFIRM_S:.0f}s confirm window"
                    )
                    return
                # Already armed. Check confirmation elapsed.
                elapsed = time.monotonic() - state.pending_exit_since_ts
                # Panic override: if drop is catastrophic right now,
                # require only 5s instead of 60s. Re-evaluated each tick:
                # a brief panic spike that recovers below 6pp returns to
                # the 60s requirement on the next call.
                _is_panic = drop_pp >= _PANIC_DROP_PP
                _required_s = _PANIC_CONFIRM_S if _is_panic else _CONFIRM_S
                if elapsed >= _required_s:
                    self._trail_triggered.add(token_address)
                    _label_tag = " [PANIC]" if _is_panic else ""
                    label = (
                        f"Dip pre-TP1 trail {pnl_pct:+.1f}% "
                        f"(peak +{peak_pct:.1f}%, confirmed {elapsed:.0f}s)"
                        f"{_label_tag}"
                    )
                    logger.info(
                        f"[PositionManager/{self.chain_name}] 🔒 PRE-TP1 CONFIRMED: "
                        f"{state.token_symbol} {label}"
                    )
                    asyncio.ensure_future(
                        self._do_pre_tp1_realtime_sell(token_address, state, label)
                    )
                    return
            else:
                # Above threshold. Disarm if pending and recovery is real.
                if state.pending_exit_since_ts is not None and drop_pp <= _RECOVERY_PP:
                    armed_for = time.monotonic() - state.pending_exit_since_ts
                    state.pending_exit_since_ts = None
                    logger.info(
                        f"[PositionManager/{self.chain_name}] 🔒 PRE-TP1 DISARMED: "
                        f"{state.token_symbol} peak +{peak_pct:.1f}% now {pnl_pct:+.1f}% "
                        f"(drop {drop_pp:.1f}pp recovered, was armed {armed_for:.0f}s)"
                    )

    async def _do_pre_tp1_realtime_sell(self, token_address: str,
                                          state: PositionState, label: str):
        """Execute the realtime pre-TP1 trail sell. Mirrors the async tail
        used by stop / TP realtime paths."""
        try:
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=label,
            )
        except Exception as e:
            logger.error(
                f"[PositionManager/{self.chain_name}] ⚡ Pre-TP1 realtime sell "
                f"failed for {state.token_symbol}: {e} — clearing trigger for retry"
            )
            self._trail_triggered.discard(token_address)

    def check_post_tp1_trail_realtime(self, token_address: str, price_usd: float):
        """Post-TP1 exhaustion trail with sub-second reaction. Called on every
        pool tick (same hook as check_exhaustion_realtime).

        Motivation: RABBIT 2026-05-16 hit peak +5.96% at t=174s, then dropped
        to -2.87% at t=178s — a 9pp give-back in 4 seconds. The existing
        5s-management-cycle post-TP1 trail (dip_winner_trail_pct) couldn't
        react. The pre-TP1 trail was moved to realtime back on 2026-05-15;
        the post-TP1 path stayed on the slow cycle. This closes the gap.

        Two-mode logic — mirrors check_exhaustion_realtime:
          1. HARD GUARD: if peak >= TP1+1pp AND pnl <= +1.0%, fire
             immediately (catches RABBIT-class fast collapses where the
             5pt move from peak happens faster than confirm window).
          2. SOFT TRAIL: if drop_pp >= dip_winner_trail_pct (1.0), arm
             with 5s confirmation. Disarm if drop tightens to 0.5pp.

        dip_buy only. Runs only when tp1_hit AND NOT tp2_hit.
        """
        token_address = token_address.lower()
        state = self._states.get(token_address)
        if not state or state.entry_price <= 0:
            return
        if state.strategy != "dip_buy":
            return
        if not state.tp1_hit or state.tp3_hit:
            return  # fires between TP1 and TP3 (post-TP1 and post-TP2 remainders)
        if token_address in self._post_tp1_trail_triggered:
            return
        if token_address in self._stop_triggered:
            return

        # Spike-rejection sanity (same as other realtime paths)
        last_rt = self._last_realtime_price.get(token_address, 0)
        ref_price = last_rt if last_rt > 0 else state.entry_price
        if ref_price > 0:
            if price_usd > ref_price * 1.20 or price_usd < ref_price * 0.80:
                return

        # Update peak (defensive; stop path also updates)
        if price_usd > state.peak_price:
            state.peak_price = price_usd

        pnl_pct = (price_usd / state.entry_price - 1) * 100
        peak_pct = (state.peak_price / state.entry_price - 1) * 100

        # 3-tier ladder threshold split (refined 2026-05-17 AM):
        #   _MIN_PEAK_SOFT = +1.5% — early soft trail with confirmation.
        #     Lowered from +3% to +1.5% on 2026-05-17 based on exit-ladder
        #     mining (n=91): 25 trades that went green +1.5 to +3.7% then
        #     crashed to -8 to -22% (avg give-back +12.78pp). Lowering the
        #     trail to +1.5% with the existing 5s confirm + 0.5pp recovery
        #     hysteresis captures them at peak - 1pp instead of -4% stop.
        #     Simulation lift: +1.12pp/trade, WR 38.5% → 47.3%.
        #     Risk: a trade that peaks +1.5%, drops to +0.4% sustained for
        #     5s, then would have rallied to +10%+ would get clipped at
        #     +0.4%. The 5s confirm + 0.5pp hysteresis is the protection.
        #     If forward shows winners getting clipped, raise to +2.0%.
        #   _MIN_PEAK_HARD = TP2 + 1pp (+6%) — only meaningful runs trigger
        #     the hard guard (fast-flip back to scratch is real for big
        #     peaks, ambiguous for small ones).
        _MIN_PEAK_SOFT = 1.5                         # +1.5% (was self.dip_tp1_pct=+3%)
        # RUNNER-TILT trail (2026-06-09): the fixed 3pp trail clips mega-runners — any
        # 3pp wiggle on the way up fires it (POKE peaked +321% but the 3pp trail would
        # exit on the first pullback; it needed a MANUAL sell, missing ~$70). Scale the
        # trail with the peak ONLY for big runners (>25% peak): give back ~25% of the
        # peak so moonshots can run. Below +25% the tuned 3pp trail is UNCHANGED (faders
        # fully protected — giveback protection preserved).
        _DROP_PP = (peak_pct * 0.25) if peak_pct > 25 else self.dip_winner_trail_pct
        _CONFIRM_S = 5.0
        _RECOVERY_PP = 0.5

        # 2026-05-18 — post-TP1 HARD GUARD removed. Universe-recorder sim
        # (n=2691) showed the guard (peak>=11 AND pnl<=+1 -> dump remainder
        # at +0.5%) cost -1.00pp/trade vs runner-tilt baseline, -$538/day
        # at $20 size. Per-fire breakdown: WITH guard locks +5.12% total
        # pnl, WITHOUT guard runner-tilt trail catches +12.97% total pnl
        # (saved per fire: -7.85pp).
        #
        # Critical: 0/343 universe fires would have gone below -3% without
        # the guard, and 343/343 (100%) resolved above +5%. Peak distribution
        # of fires: median +19%, P90 +48%, P95 +78%. Pure runner-clipping.
        #
        # All 25 threshold combos tested (peak>={8..30}, pnl<={-2..+3}) were
        # net-negative — best at -0.54pp/trade. Removing this lets the trail
        # leg deliver runner-tilt's tail capture as designed.

        # SOFT TRAIL with confirmation window (covers +3-6% peak cohort too)
        if peak_pct >= _MIN_PEAK_SOFT:
            drop_pp = peak_pct - pnl_pct
            if drop_pp >= _DROP_PP:
                if state.post_tp1_pending_ts is None:
                    state.post_tp1_pending_ts = time.monotonic()
                    logger.info(
                        f"[PositionManager/{self.chain_name}] 🔒 POST-TP1 ARMED: "
                        f"{state.token_symbol} peak +{peak_pct:.1f}% now {pnl_pct:+.1f}% "
                        f"(drop {drop_pp:.1f}pp) — {_CONFIRM_S:.0f}s confirm window"
                    )
                elapsed = time.monotonic() - state.post_tp1_pending_ts
                if elapsed >= _CONFIRM_S:
                    self._post_tp1_trail_triggered.add(token_address)
                    label = (
                        f"Dip post-TP1 trail {pnl_pct:+.1f}% "
                        f"(peak +{peak_pct:.1f}%, confirmed {elapsed:.0f}s)"
                    )
                    logger.info(
                        f"[PositionManager/{self.chain_name}] 🔒 POST-TP1 CONFIRMED: "
                        f"{state.token_symbol} {label}"
                    )
                    asyncio.ensure_future(
                        self._do_post_tp1_realtime_sell(token_address, state, label)
                    )
                    return
            else:
                if state.post_tp1_pending_ts is not None and drop_pp <= _RECOVERY_PP:
                    armed_for = time.monotonic() - state.post_tp1_pending_ts
                    state.post_tp1_pending_ts = None
                    logger.info(
                        f"[PositionManager/{self.chain_name}] 🔒 POST-TP1 DISARMED: "
                        f"{state.token_symbol} peak +{peak_pct:.1f}% now {pnl_pct:+.1f}% "
                        f"(drop {drop_pp:.1f}pp recovered, was armed {armed_for:.0f}s)"
                    )

    async def _do_post_tp1_realtime_sell(self, token_address: str,
                                          state: PositionState, label: str):
        """Execute the realtime post-TP1 trail sell."""
        try:
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=label,
            )
        except Exception as e:
            logger.error(
                f"[PositionManager/{self.chain_name}] ⚡ Post-TP1 realtime sell "
                f"failed for {state.token_symbol}: {e} — clearing trigger for retry"
            )
            self._post_tp1_trail_triggered.discard(token_address)

    async def _evaluate_scalp(self, token_address: str, state: PositionState):
        """
        Scalp branch (4-phase rewrite):
          - Hard stop at -scalp_stop_pct (6%) → full close
          - Time exit: after scalp_time_exit_candles (4) 5m candles from entry_close_time,
            if pnl < scalp_time_exit_min_pct (5%) → full close
          - Safety belt: scalp_max_hold_minutes (45) → full close
          - TP2: pnl ≥ scalp_tp2_pct (15%) AND tp1_hit → sell scalp_tp2_sell (35%)
          - TP1: pnl ≥ scalp_tp1_pct (10%) → sell scalp_tp1_sell (50%), set tp1_hit
          - Runner: after TP2, no further action (winner_trail handled externally)
        """
        if token_address in self._stop_triggered:
            return
        pnl_pct = state.pnl_pct
        meta = getattr(state, "scalp_meta", None) or {}

        # 1) Hard stop
        if pnl_pct <= -self.scalp_stop_pct:
            logger.warning(
                f"[PositionManager/{self.chain_name}] 🛑 SCALP HARD STOP: "
                f"{state.token_symbol} at {pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Scalp hard stop -{self.scalp_stop_pct:.1f}%"
            )
            self.stop_loss_hits += 1
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "stop_loss", pnl_usd)
            return

        # 2) Time exit — N 5m candles since entry candle close without +X% move
        entry_close_time = meta.get("entry_close_time")
        if entry_close_time:
            now_ts = datetime.now(timezone.utc).timestamp()
            candles_elapsed = (now_ts - float(entry_close_time)) / 300.0
            if (candles_elapsed >= self.scalp_time_exit_candles
                    and pnl_pct < self.scalp_time_exit_min_pct):
                logger.info(
                    f"[PositionManager/{self.chain_name}] ⏱ SCALP TIME EXIT: "
                    f"{state.token_symbol} @ {pnl_pct:.1f}% after "
                    f"{candles_elapsed:.1f} candles (<{self.scalp_time_exit_min_pct:.0f}%)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"Scalp time exit {candles_elapsed:.1f}c @ {pnl_pct:.1f}%"
                )
                if self.scalp_queue:
                    pnl_usd = state.position_size_usd * pnl_pct / 100
                    self.scalp_queue.on_scalp_close(token_address, "scalp_time_exit", pnl_usd)
                return

        # 3) Safety belt — 45min absolute max
        hold_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        if hold_seconds >= self.scalp_max_hold_minutes * 60:
            logger.info(
                f"[PositionManager/{self.chain_name}] ⏱ SCALP MAX HOLD: "
                f"{state.token_symbol} after {hold_seconds/60:.0f}min"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Scalp max hold {hold_seconds/60:.0f}min"
            )
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "scalp_max_hold", pnl_usd)
            return

        # 4) TP2 — after TP1, at +15%, sell 35% of remaining. The remaining
        # position size is small after the TP2 cut; if it's the final exit
        # action for the trade we still need to release the capital slot so
        # ScalpCapitalManager._open doesn't accumulate stale entries on
        # winning streaks. The runner half (post-TP2) is handled by the
        # winner_trail in the dip path; for scalp we treat TP2 as full close.
        if state.tp1_hit and not state.tp2_hit and pnl_pct >= self.scalp_tp2_pct:
            state.tp2_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 SCALP TP2: "
                f"{state.token_symbol} +{pnl_pct:.1f}% sell {self.scalp_tp2_sell*100:.0f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.scalp_tp2_sell,
                reason=f"Scalp TP2 +{pnl_pct:.1f}%"
            )
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "scalp_tp2", pnl_usd)
            return

        # 5) TP1 — at +10%, sell 50%. Partial close — do NOT release the
        # capital slot; runner half is still open and will exit via TP2,
        # stop, time, or max-hold.
        if not state.tp1_hit and pnl_pct >= self.scalp_tp1_pct:
            state.tp1_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 SCALP TP1: "
                f"{state.token_symbol} +{pnl_pct:.1f}% sell {self.scalp_tp1_sell*100:.0f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.scalp_tp1_sell,
                reason=f"Scalp TP1 +{pnl_pct:.1f}%"
            )
            return

        # 6) Runner — past TP2, external winner_trail handles exits

    async def _execute_sell(self, token_address: str,
                             state: PositionState,
                             pct: float, reason: str):
        """Execute a sell through the main trader."""
        try:
            # Mirror state TP flags to Position before the sell. trader.sell()
            # calls _save_open_positions() afterwards, so this is the persist
            # point. Without it, state.tp1_hit (memory-only) is lost on restart
            # and Dip TP1 re-fires, halving the position every redeploy.
            _pos = self.open_positions_ref.get(token_address)
            if _pos is not None:
                _pos.take_profit_1_hit = bool(state.tp1_hit)
                _pos.take_profit_2_hit = bool(state.tp2_hit)
            await self.trader.sell(
                token_address=token_address,
                token_symbol=state.token_symbol,
                reason=reason,
                pct=pct
            )
            # Sync state.position_size_usd to the post-sell remaining size for
            # partial sells. trader.sell() reduces position.amount_usd by
            # (1 - pct) but state.position_size_usd was previously stale, so
            # downstream pnl_usd calculations (e.g. scalp_capital.record_close)
            # would over-report by the un-sold proportion. Full closes (pct
            # >= 1.0) drop the state entirely below.
            if 0 < pct < 1.0:
                state.position_size_usd = state.position_size_usd * (1.0 - pct)
            if pct >= 1.0 and token_address in self._states:
                # Universal 60-min cross-strategy cooldown on ALL full closes
                # (TP, time exit, manual, etc) — diversifies rotation and prevents
                # back-to-back re-buys of the same token. max() in register_stop_loss
                # preserves any existing longer cooldown (24h flash crash, 2h realtime stop).
                if self.scanner:
                    try:
                        self.scanner.register_stop_loss(
                            token_address=token_address,
                            token_symbol=state.token_symbol,
                            exit_price=state.current_price,
                            cooldown_seconds=3600,
                        )
                    except Exception as e:
                        logger.warning(
                            f"[PositionManager/{self.chain_name}] "
                            f"Close-cooldown register failed for {state.token_symbol}: {e}"
                        )
                # Peak recorder finalize with REAL exit reason and pnl.
                # Use position_size_usd (pre-sell) and current_price vs entry_price
                # to estimate pnl at this final close. _execute_sell is the only
                # full-close hook that has both `reason` (the exit cause string)
                # and `state.current_price` (price at close decision).
                try:
                    from core.peak_recorder import get_recorder
                    _entry = state.entry_price or 0.0
                    _cur = state.current_price or 0.0
                    _pnl_pct = ((_cur / _entry) - 1) * 100 if _entry > 0 else 0.0
                    _orig_size = state.original_size_usd or state.position_size_usd or 0.0
                    _est_dollar_pnl = _orig_size * (_pnl_pct / 100.0)
                    get_recorder().finalize(
                        token_address,
                        exit_reason=reason,
                        exit_pnl=_est_dollar_pnl,
                        exit_time=datetime.now(timezone.utc),
                    )
                except Exception:
                    pass
                del self._states[token_address]
                self._stop_triggered.discard(token_address)
        except Exception as e:
            logger.error(
                f"[PositionManager/{self.chain_name}] Sell error: {e}"
            )
            raise  # re-raise so callers (e.g. _do_realtime_stop) can handle retry

    async def _execute_pyramid(self, token_address: str,
                               state, pnl_pct: float):
        """Add 50% of original position size after TP1 when volume still healthy."""
        # Don't pyramid a pyramid — each [PYRAMID] position gets a fresh PositionState
        # with pyramided=False, which would trigger an infinite chain. Block it here.
        if "[PYRAMID]" in state.token_symbol:
            return
        try:
            add_usd = state.original_size_usd * 0.50
            logger.info(
                f"[PositionManager/{self.chain_name}] 📈 PYRAMID: "
                f"{state.token_symbol} +{pnl_pct:.1f}% — "
                f"vol healthy, adding ${add_usd:.0f} (50% of original)"
            )
            await self.trader.buy(
                token_address=token_address,
                token_symbol=f"{state.token_symbol}[PYRAMID]",
                reason=f"Pyramid TP1 +{pnl_pct:.1f}% — volume healthy",
                override_usd=add_usd,
            )
            state.pyramided = True
            await self.telegram.send(
                f"📈 *Pyramid* [{self.chain_name}]\n\n"
                f"🪙 ${state.token_symbol}\n"
                f"📊 PnL: +{pnl_pct:.1f}%\n"
                f"💰 Adding: ${add_usd:.0f} (50% of original)\n"
                f"💧 Volume: healthy at TP1\n"
                f"⚠️ One tranche only — no second pyramid"
            )
        except Exception as e:
            logger.error(f"[PositionManager] Pyramid error: {e}")

    def get_stats(self) -> dict:
        return {
            "chain": self.chain_name,
            "open_positions": len(self._states),
            "tp1_hits": self.tp1_hits,
            "tp2_hits": self.tp2_hits,
            "tp3_hits": self.tp3_hits,
            "stop_loss_hits": self.stop_loss_hits,
            "stall_exits": self.stall_exits,
            "avg_downs": self.avg_downs
        }

    def get_position_detail(self, token_address: str) -> Optional[dict]:
        state = self._states.get(token_address)
        if not state:
            return None
        return {
            "symbol": state.token_symbol,
            "pnl_pct": round(state.pnl_pct, 2),
            "hours_open": round(state.hours_open, 1),
            "tp1_hit": state.tp1_hit,
            "tp2_hit": state.tp2_hit,
            "tp3_hit": state.tp3_hit,
            "averaged_down": state.averaged_down,
            "stall_exit_done": state.stall_exit_done,
            "volume_declining": state.volume_declining,
            "current_price": state.current_price,
            "peak_price": state.peak_price,
            "is_micro_cap": state.is_micro_cap,
            "breakeven_locked": state.breakeven_locked
        }
