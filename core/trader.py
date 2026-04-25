"""
Trader
Handles buy/sell execution on Solana via Jupiter aggregator.
Manages open positions with take-profit and stop-loss logic.
"""

import asyncio
import logging
import aiohttp
import json
import base64
import time
import os
from typing import Dict, Optional, Set
from datetime import datetime, timezone
from dataclasses import dataclass, field

from core.paper_slippage import PaperSlippageSimulator

logger = logging.getLogger(__name__)

# Paid API key endpoints (api.jup.ag) — more reliable, higher rate limits
# Falls back to free tier (quote-api.jup.ag) if no key is set
import os as _os
_JUPITER_API_KEY = _os.environ.get("JUPITER_API_KEY", "")
if _JUPITER_API_KEY:
    JUPITER_QUOTE_API = f"https://api.jup.ag/swap/v1/quote"
    JUPITER_SWAP_API = f"https://api.jup.ag/swap/v1/swap"
    _JUPITER_HEADERS = {"x-api-key": _JUPITER_API_KEY}
else:
    JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
    JUPITER_SWAP_API = "https://quote-api.jup.ag/v6/swap"
    _JUPITER_HEADERS = {}
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@dataclass
class Position:
    token_address: str
    token_symbol: str
    entry_price_usd: float
    amount_tokens: float
    amount_sol_spent: float
    entry_time: datetime
    reason: str
    take_profit_1_hit: bool = False
    take_profit_2_hit: bool = False
    current_price_usd: float = 0.0
    current_price_ts: float = 0.0   # time.time() of last position_manager price sync
    pnl_usd: float = 0.0
    # Signal quality at entry — used by PositionManager for pyramid decisions
    signal_score: int = 0
    hh_hl_confirmed: bool = False
    # Metadata for dashboard and tracker
    chain_id: str = "solana"
    amount_usd: float = 0.0   # USD position size at entry (not SOL)
    strategy: str = "scanner"  # Which strategy placed this trade
    pair_address: str = ""     # DEX pool address — used for correct DexScreener chart link
    min_price_usd: float = 0.0   # Lowest price seen during hold — set to entry_price by PositionManager at init
    entry_time_monotonic: float = 0.0  # time.monotonic() at entry — used for hold duration calc
    entry_market_cap_usd: float = 0.0  # Market cap at entry — for performance analysis by MC range
    entry_age_hours: float = 0.0       # Token pair age in hours at entry — for performance analysis by age
    entry_volume_h1_usd: float = 0.0   # 1h volume at entry — for performance analysis by activity level
    scalp_meta: Optional[dict] = None  # 4-phase scalper: sweep_low/stop/tp1/entry_close_time/pool_address
    # Generic entry-time snapshot (Batch 1 added 2026-04-25): liquidity_usd,
    # protocol, peak_h24_6h, cycles_seen_before_buy, avg_trade_size_h1_usd, etc.
    # Anything dip_scanner has at evaluation time but doesn't merit its own field.
    entry_meta: Optional[dict] = None


_DATA_DIR = os.environ.get("DATA_DIR", ".")
_REENTRY_STATE_FILE = os.path.join(_DATA_DIR, "reentry_state.json")


class ReentryTracker:
    """Persistent re-entry state — survives redeploys. Keyed by token_address.lower()."""
    def __init__(self):
        # Addresses fully exited at least once — persisted to disk
        self.previously_held: Set[str] = set()
        # Per-address buy count (all strategies, all paths)
        self.buy_counts: dict = {}
        # Last known h1% at time of entry, per address
        self.last_h1_pct: dict = {}
        self._load()

    def _load(self):
        # Load persisted set from disk
        if os.path.exists(_REENTRY_STATE_FILE):
            try:
                with open(_REENTRY_STATE_FILE) as f:
                    data = json.load(f)
                self.previously_held = set(data.get("previously_held", []))
            except Exception as e:
                logger.warning(f"[ReentryTracker] Failed to load state: {e}")

        # Bootstrap from today's trades — add any token that hit a stop-loss today
        _trades_file = os.path.join(_DATA_DIR, "trades.json")
        if os.path.exists(_trades_file):
            try:
                with open(_trades_file) as f:
                    trades = json.load(f)
                today = datetime.now(timezone.utc).date()
                for t in trades:
                    if t.get("type") != "sell":
                        continue
                    reason = t.get("reason", "").lower()
                    if "stop" not in reason and "realtime" not in reason:
                        continue
                    ts = t.get("time", "")
                    try:
                        if datetime.fromisoformat(ts).date() != today:
                            continue
                    except Exception:
                        continue
                    addr = t.get("address", "").lower()
                    if addr:
                        self.previously_held.add(addr)
            except Exception as e:
                logger.warning(f"[ReentryTracker] Failed to bootstrap from trades: {e}")

        logger.info(
            f"[ReentryTracker] Loaded {len(self.previously_held)} previously-held tokens"
        )

    def save(self):
        try:
            with open(_REENTRY_STATE_FILE, "w") as f:
                json.dump({"previously_held": list(self.previously_held)}, f)
        except Exception as e:
            logger.warning(f"[ReentryTracker] Failed to save state: {e}")


class Trader:
    def __init__(self, private_key: str, rpc_url: str, tracker, telegram, risk_manager,
                 stop_loss_pct: float = 10.0, kill_switch=None):
        self.private_key = private_key
        self.rpc_url = rpc_url
        self.tracker = tracker
        self.telegram = telegram
        self.risk_manager = risk_manager
        self.kill_switch = kill_switch
        self.open_positions: Dict[str, Position] = {}
        self.session: Optional[aiohttp.ClientSession] = None

        # Take profit levels (from config)
        self.tp1_multiplier = 2.0    # Sell 50% at 2x
        self.tp2_multiplier = 5.0    # Sell 30% at 5x
        self.tp3_multiplier = 10.0   # Sell rest at 10x
        self.stop_loss_pct = stop_loss_pct

        # Paper trading slippage simulator
        self.paper_slippage = PaperSlippageSimulator("solana")

        # Sell dedup — prevents CopyTrader and PositionManager racing on same token
        self._selling: set = set()

        # Buy dedup — prevents concurrent signals double-entering the same token
        self._buying: set = set()

        # DipWatcher reservation — tokens claimed by DipWatcher while waiting for
        # dip+recovery.  Any other scanner path must not buy while reserved.
        self._dip_watching: set = set()

        # Session re-entry tracking
        self.reentry = ReentryTracker()

        # Per-token cooldown after a losing dip_buy close.  Maps token_addr (lower)
        # to time.time() (wall-clock) at the moment of the loss.  Persisted to
        # /data/dip_loss_cooldown.json so deploys don't wipe protection.
        self._dip_loss_cooldown: Dict[str, float] = {}
        self._dip_loss_cooldown_path = os.path.join(
            os.environ.get("DATA_DIR", "/data"), "dip_loss_cooldown.json"
        )
        self._load_dip_loss_cooldown()

        pass  # daily buy limit removed — entry quality handles repeat buys

        # Optional Axiom auth — registered externally for Axiom-based price lookups
        self._axiom_auth = None

        # Optional Axiom real-time price feed (Phase 4)
        self._axiom_price_feed = None

        # Optional DexScreener real-time price feed (sub-second stop-loss accuracy)
        self._dex_price_feed = None

        # Optional Solana RPC + Jupiter price feed (0.5s, covers all pool types)
        self._rpc_price_feed = None

        # Optional security checker — used for LP re-verification at buy time
        self._security_checker = None

        # NOTE: Internal _monitor_positions is DISABLED — PositionManager handles
        # all TP/SL logic with the user's exact config-driven rules.
        # asyncio.create_task(self._monitor_positions())

    def is_dip_in_cooldown(self, token_address: str, window_seconds: float) -> bool:
        """
        Return True if this token had a losing dip_buy close within the last
        `window_seconds`.  Used by DipScanner to skip same-token rebuys.
        Wall-clock based so the cooldown survives process restarts.
        """
        addr = token_address.lower()
        ts = self._dip_loss_cooldown.get(addr)
        if ts is None:
            return False
        return (time.time() - ts) < window_seconds

    def _load_dip_loss_cooldown(self) -> None:
        """Load persisted dip-loss cooldown timestamps; prune entries >24h old."""
        try:
            if os.path.exists(self._dip_loss_cooldown_path):
                with open(self._dip_loss_cooldown_path) as f:
                    raw = json.load(f)
                cutoff = time.time() - 86400  # drop anything older than 24h
                self._dip_loss_cooldown = {
                    k: float(v) for k, v in raw.items()
                    if isinstance(v, (int, float)) and float(v) > cutoff
                }
                if self._dip_loss_cooldown:
                    logger.info(
                        f"[Trader] Loaded {len(self._dip_loss_cooldown)} "
                        f"dip-loss cooldowns from disk"
                    )
        except Exception as e:
            logger.warning(f"[Trader] Could not load dip_loss_cooldown.json: {e}")
            self._dip_loss_cooldown = {}

    def _save_dip_loss_cooldown(self) -> None:
        """Write cooldown dict atomically (tmp + rename)."""
        try:
            os.makedirs(os.path.dirname(self._dip_loss_cooldown_path), exist_ok=True)
            tmp_path = self._dip_loss_cooldown_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(self._dip_loss_cooldown, f)
            os.replace(tmp_path, self._dip_loss_cooldown_path)
        except Exception as e:
            logger.warning(f"[Trader] Could not save dip_loss_cooldown.json: {e}")

    def register_axiom_auth(self, auth):
        """Register Axiom auth manager for Axiom-based price lookups."""
        self._axiom_auth = auth

    def register_axiom_price_feed(self, feed):
        """
        Register the AxiomPriceFeed instance for real-time price updates.
        The position manager can call:
            price = self.trader._axiom_price_feed.price_cache.get(token_address)
        before falling back to DexScreener.
        """
        self._axiom_price_feed = feed

    def register_dex_price_feed(self, feed):
        """Register the DexScreener real-time PriceFeed for sub-second stop-loss accuracy."""
        self._dex_price_feed = feed

    def register_rpc_price_feed(self, feed):
        """Register the Solana RPC + Jupiter price feed (0.5s, covers all pool types)."""
        self._rpc_price_feed = feed

    def register_security_checker(self, checker):
        """Register the security checker for LP re-verification at buy time."""
        self._security_checker = checker

    async def buy(self, token_address: str, token_symbol: str,
                  reason: str, signal_score: int = 0,
                  hh_hl_confirmed: bool = False,
                  chain_id: str = "solana", strategy: str = "scanner",
                  override_usd: float = 0.0, pair_address: str = "",
                  market_cap_usd: float = 0.0, age_hours: float = 0.0,
                  volume_h1_usd: float = 0.0,
                  override_impact_pct: float = -1.0,
                  scalp_meta: Optional[dict] = None,
                  entry_meta: Optional[dict] = None):
        """Execute a buy order."""
        if os.environ.get("TRADING_PAUSED", "").lower() in ("true", "1", "yes"):
            logger.info(f"[Trader] Buy blocked — TRADING_PAUSED=true ({strategy}/{token_symbol})")
            return
        if self.kill_switch and self.kill_switch.is_active:
            logger.info(f"[Trader] Buy blocked — kill switch active ({self.kill_switch._kill_reason})")
            return

        _allow = os.environ.get("STRATEGY_ALLOWLIST", "").strip()
        if _allow:
            _allowed = {s.strip() for s in _allow.split(",") if s.strip()}
            if strategy not in _allowed:
                logger.info(
                    f"[Trader] Buy blocked — strategy '{strategy}' not in "
                    f"STRATEGY_ALLOWLIST={sorted(_allowed)} ({token_symbol})"
                )
                return

        if token_address.lower() in self._buying:
            logger.info(f"[Trader] Buy already in flight for {token_symbol}, skipping")
            return
        self._buying.add(token_address.lower())

        try:
            if self.risk_manager.is_daily_limit_hit():
                logger.warning(f"Risk manager blocked buy for {token_symbol} — daily limit hit")
                return
            if override_usd > 0:
                if strategy == "dip_buy":
                    # Dip buys use a fixed $500 size — don't cap at scanner's max_position_pct
                    position_size_usd = override_usd
                elif strategy == "scalp":
                    # Scalp has its own capital pool (ScalpCapitalManager); never clip
                    # against main risk manager's pool.
                    position_size_usd = override_usd
                else:
                    # Cap override at risk manager's normal max to prevent inflated rebuys
                    risk_max = self.risk_manager.available_capital * self.risk_manager.max_position_pct
                    position_size_usd = min(override_usd, risk_max)
            else:
                position_size_usd = self.risk_manager.get_position_size()
            if position_size_usd <= 0:
                logger.warning(f"Risk manager blocked buy for {token_symbol}")
                return

            _addr_lower = token_address.lower()
            if _addr_lower in self.open_positions:
                logger.info(
                    f"[Trader] Buy blocked for {token_symbol} "
                    f"— position already open"
                )
                return
            if _addr_lower in self._dip_watching:
                logger.info(
                    f"[Trader] Buy blocked for {token_symbol} "
                    f"— reserved by DipWatcher"
                )
                return

            # ── Fix 1: LP re-check at execution ────────────────────────────
            # Re-verify LP is still locked right before buy — devs sometimes
            # lock LP briefly to pass scanners then unlock after accumulating buyers.
            # Fail-open on timeout/API error so a slow rugcheck doesn't kill good trades.
            # Skip for graduation strategy: pump.fun LP is auto-burned at graduation,
            # but rugcheck indexer hasn't processed the tx yet when we buy (<1s after).
            if self._security_checker is not None and strategy != "graduation":
                try:
                    _rc = await self._security_checker._fetch_rugcheck(token_address)
                    if _rc and not _rc.get("_invalid_address"):
                        _lp_pct = _rc.get("lpLockedPct")
                        if _lp_pct is not None and float(_lp_pct or 0) == 0.0:
                            logger.warning(
                                f"[Trader] LP UNLOCK BLOCK: {token_symbol} "
                                f"({token_address[:8]}…) — LP unlocked since scan, skipping buy"
                            )
                            return
                except Exception:
                    pass  # fail-open — never block a buy due to rugcheck API failure

            # ── Fix 2: Real-time volume floor at execution ──────────────────
            # If the Axiom WS has been tracking this token (deferred/DipWatcher paths)
            # and tick count in last 60s is zero, volume has dried up — skip.
            if self._axiom_price_feed is not None:
                _tick_60 = self._axiom_price_feed.get_tick_count(token_address, 60)
                _tick_120 = self._axiom_price_feed.get_tick_count(token_address, 120)
                # Only apply if we have any history (token was pre-subscribed)
                if _tick_120 > 0 and _tick_60 == 0:
                    logger.info(
                        f"[Trader] Volume dead-check: {token_symbol} — "
                        f"0 ticks in last 60s (had {_tick_120} in 120s) — skipping"
                    )
                    return

            logger.info(f"💚 Buying {token_symbol} — ${position_size_usd:.0f} — {reason}")

            # ── PAPER TRADING MODE ────────────────────────────────────
            if not self.private_key:
                # Subscribe to real-time price feeds for this token
                logger.info(
                    f"[Trader/paper] subscribing feeds for {token_symbol} "
                    f"({strategy}): axiom={self._axiom_price_feed is not None} "
                    f"dex={self._dex_price_feed is not None} "
                    f"rpc={self._rpc_price_feed is not None}"
                )
                if self._axiom_price_feed is not None:
                    self._axiom_price_feed.subscribe_token(token_address)
                if self._dex_price_feed is not None:
                    self._dex_price_feed.subscribe_token(token_address)
                # RPC + Jupiter feed: pass pool_type hint from reason for pump.fun detection
                if self._rpc_price_feed is not None:
                    _proto = ""
                    if "pump amm" in reason.lower():
                        _proto = "pump amm"
                    self._rpc_price_feed.subscribe_token(token_address, pool_type=_proto)

                sol_amount = await self._usd_to_sol(position_size_usd)
                if sol_amount <= 0:
                    logger.error(f"Could not convert USD→SOL for {token_symbol} — buy aborted")
                    return

                # Get current price (Axiom cache → Jupiter price API → DexScreener)
                # Graduation buys: fresh graduates aren't indexed yet — skip this check
                # and derive entry price from the Jupiter quote below instead.
                current_price = await self._get_token_price(token_address)
                if current_price <= 0 and strategy != "graduation":
                    logger.error(f"Could not get price for {token_symbol} — buy aborted")
                    return

                # Try Jupiter Quote solely for price-impact percentage.
                # Token unit accounting stays on the DexScreener-scale price to avoid
                # decimal-precision mismatches (pump.fun tokens are 6-decimal, not 9).
                entry_price    = 0.0
                tokens_received = 0.0
                impact_pct     = 0.0
                price_source   = "unknown"

                if override_impact_pct >= 0 and current_price > 0:
                    # Caller already has the correct Jupiter impact — reuse it
                    impact_pct      = override_impact_pct / 100.0
                    entry_price     = current_price * (1.0 + impact_pct)
                    tokens_received = position_size_usd / entry_price
                    price_source    = "axiom_impact+price"
                elif override_impact_pct < 0:
                    quote = await self._get_quote(SOL_MINT, token_address, int(sol_amount * 1e9))
                    if quote:
                        raw_impact = float(quote.get("priceImpactPct", 0))
                        if raw_impact >= 0 and current_price > 0:
                            impact_pct  = raw_impact
                            entry_price = current_price * (1.0 + impact_pct)
                            tokens_received = position_size_usd / entry_price
                            price_source = "jupiter_impact+dex_price"

                if entry_price <= 0 and current_price > 0:
                    # Fallback: DexScreener price + slippage model
                    liquidity_usd = await self._get_token_liquidity(token_address)
                    entry_price, tokens_received, slip_est = \
                        self.paper_slippage.apply_to_buy(
                            position_size_usd, liquidity_usd, current_price, token_symbol
                        )
                    impact_pct   = slip_est.total_slippage_pct
                    price_source = "dexscreener+model"

                if entry_price <= 0 or tokens_received <= 0:
                    logger.error(
                        f"[PAPER] Cannot price {token_symbol} — "
                        f"current_price=${current_price:.8f}, override_impact={override_impact_pct} — buy aborted"
                    )
                    return

                position = Position(
                    token_address=token_address.lower(),
                    token_symbol=token_symbol,
                    entry_price_usd=entry_price,
                    amount_tokens=tokens_received,
                    amount_sol_spent=sol_amount,
                    entry_time=datetime.now(timezone.utc),
                    reason=reason,
                    signal_score=signal_score,
                    hh_hl_confirmed=hh_hl_confirmed,
                    chain_id=chain_id,
                    amount_usd=position_size_usd,
                    strategy=strategy,
                    pair_address=pair_address,
                    min_price_usd=entry_price,
                    entry_time_monotonic=time.monotonic(),
                    entry_market_cap_usd=market_cap_usd,
                    entry_age_hours=age_hours,
                    entry_volume_h1_usd=volume_h1_usd,
                    scalp_meta=scalp_meta,
                    entry_meta=entry_meta,
                )
                self.open_positions[token_address.lower()] = position
                self.reentry.buy_counts[token_address.lower()] = self.reentry.buy_counts.get(token_address.lower(), 0) + 1
                if strategy != "scalp":
                    self.risk_manager.record_buy(position_size_usd)

                await self.telegram.send(
                    f"📄 *[PAPER] Bought ${token_symbol}*\n\n"
                    f"💵 Size: ${position_size_usd:.0f}\n"
                    f"💰 Entry: ${entry_price:.8f} "
                    f"({impact_pct:+.2f}% impact via {price_source})\n"
                    f"🪙 Tokens: {tokens_received:.4f}\n"
                    f"📝 {reason}"
                )
                self.tracker.record_buy(position)
                logger.info(
                    f"📄 [PAPER] Bought {token_symbol} — "
                    f"${position_size_usd:.0f} | "
                    f"Impact: {impact_pct:+.2f}% | Source: {price_source}"
                )
                return

            # ── LIVE TRADING MODE ─────────────────────────────────────
            # Get SOL amount for position size
            sol_amount = await self._usd_to_sol(position_size_usd)
            if sol_amount <= 0:
                return

            # Get Jupiter quote
            quote = await self._get_quote(
                input_mint=SOL_MINT,
                output_mint=token_address,
                amount=int(sol_amount * 1e9)  # lamports
            )
            if not quote:
                logger.error(f"No quote available for {token_symbol}")
                return

            # Execute swap
            out_amount = int(quote.get("outAmount", 0))
            entry_price = position_size_usd / (out_amount / 1e9) if out_amount > 0 else 0

            success = await self._execute_swap(quote)
            if not success:
                logger.error(f"Swap failed for {token_symbol}")
                return

            # Record position
            position = Position(
                token_address=token_address.lower(),
                token_symbol=token_symbol,
                entry_price_usd=entry_price,
                amount_tokens=out_amount / 1e9,
                amount_sol_spent=sol_amount,
                entry_time=datetime.now(timezone.utc),
                reason=reason,
                signal_score=signal_score,
                hh_hl_confirmed=hh_hl_confirmed,
                chain_id=chain_id,
                amount_usd=position_size_usd,
                strategy=strategy,
                pair_address=pair_address,
                min_price_usd=entry_price,
                entry_time_monotonic=time.monotonic(),
                entry_market_cap_usd=market_cap_usd,
                entry_age_hours=age_hours,
                entry_volume_h1_usd=volume_h1_usd,
                scalp_meta=scalp_meta,
                entry_meta=entry_meta,
            )
            self.open_positions[token_address.lower()] = position
            self.reentry.buy_counts[token_address.lower()] = self.reentry.buy_counts.get(token_address.lower(), 0) + 1
            self.risk_manager.record_buy(position_size_usd)

            # Subscribe real-time price feeds for live position
            if self._axiom_price_feed is not None:
                self._axiom_price_feed.subscribe_token(token_address)
            if self._dex_price_feed is not None:
                self._dex_price_feed.subscribe_token(token_address)
            if self._rpc_price_feed is not None:
                _proto = "pump amm" if "pump amm" in reason.lower() else ""
                self._rpc_price_feed.subscribe_token(token_address, pool_type=_proto)

            await self.telegram.send(
                f"✅ *Bought ${token_symbol}*\n\n"
                f"💵 Size: ${position_size_usd:.0f}\n"
                f"📝 Reason: {reason}\n"
                f"🎯 TP1: {self.tp1_multiplier}x | TP2: {self.tp2_multiplier}x | TP3: {self.tp3_multiplier}x\n"
                f"🛑 Stop Loss: -{self.stop_loss_pct*100:.0f}%"
            )
            self.tracker.record_buy(position)
            logger.info(f"✅ Bought {token_symbol} — ${position_size_usd:.0f}")

        except Exception as e:
            logger.error(f"Buy failed for {token_symbol}: {e}")
        finally:
            self._buying.discard(token_address.lower())

    async def sell(self, token_address: str, token_symbol: str, reason: str, pct: float = 1.0):
        """Execute a sell order for a percentage of the position."""
        token_address = token_address.lower()
        position = self.open_positions.get(token_address)
        if not position:
            logger.warning(f"No position found for {token_symbol}")
            return

        # Prevent concurrent sells on the same token (race between CopyTrader and PositionManager)
        if token_address in self._selling:
            logger.debug(f"[Trader] Sell already in progress for {token_symbol} — skipping duplicate")
            return
        self._selling.add(token_address)

        try:
            # ── PAPER TRADING MODE ────────────────────────────────────
            if not self.private_key:
                tokens_to_sell = position.amount_tokens * pct

                # Paper sell: use Axiom cache / Jupiter price API / DexScreener + slippage model.
                # Jupiter swap quotes are skipped here because `tokens_to_sell` is in
                # DexScreener-scale human units and token decimal precision varies
                # (6 for most pump.fun tokens, 9 for SOL-native tokens), making
                # atomic-unit conversion unreliable without on-chain metadata.
                # Dead liquidity: pool is empty, tokens can't be sold — full loss.
                # Bypass price fetch entirely; recording any non-zero price would
                # produce fake profit when the API still shows a stale price.
                is_dead_liquidity = "Dead liquidity" in reason
                if is_dead_liquidity:
                    cost_basis   = position.entry_price_usd * tokens_to_sell
                    usd_received = 0.0
                    exit_price   = 0.0
                    pnl          = -cost_basis
                    pnl_pct      = -100.0
                    impact_pct   = 0.0
                    price_source = "dead_liquidity"
                    logger.warning(
                        f"[Trader] Dead liquidity sell: {token_symbol} — "
                        f"recording full loss ${-pnl:.2f} (cost ${cost_basis:.2f})"
                    )
                else:
                    # Prefer position_manager's synced price (from Axiom/RPC — same source
                    # the dashboard uses) so sell P&L matches what the user saw.
                    _synced_price = getattr(position, "current_price_usd", 0)
                    _synced_ts    = getattr(position, "current_price_ts", 0)
                    if _synced_price > 0 and (time.time() - _synced_ts) < 10.0:
                        current_price = _synced_price
                    else:
                        current_price = await self._get_token_price(token_address)
                    # Last resort: entry price avoids a bogus 100% loss on API failure
                    if current_price <= 0:
                        current_price = getattr(position, "current_price_usd", 0) or position.entry_price_usd
                        logger.warning(
                            f"[Trader] Price=0 for {token_symbol} at paper sell — "
                            f"falling back to last known price ${current_price:.8f}"
                        )
                    # Sanity check: if price implies >20x gain, cross-validate with DexScreener.
                    # Guards against phantom gains from price feed glitches (wrong units, stale
                    # cache, API returning SOL price instead of USD, etc.).
                    # If DexScreener can't confirm, ABORT the sell — no sell is better than
                    # a $17M phantom profit corrupting the P&L record.
                    if position.entry_price_usd > 0 and current_price > position.entry_price_usd * 20:
                        _gain_x = current_price / position.entry_price_usd
                        _san_confirmed = False
                        try:
                            dex_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                            async with aiohttp.ClientSession() as _san_sess:
                                async with _san_sess.get(dex_url, timeout=aiohttp.ClientTimeout(total=5)) as _san_resp:
                                    _san_data = await _san_resp.json(content_type=None)
                            _san_pairs = _san_data.get("pairs") or []
                            if _san_pairs:
                                _san_best = max(_san_pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0))
                                dex_price = float(_san_best.get("priceUsd", 0) or 0)
                                if dex_price > 0 and dex_price < current_price * 0.5:
                                    logger.warning(
                                        f"[Trader] ⚠️ Paper sell price sanity FAILED: {token_symbol} — "
                                        f"primary=${current_price:.8f} vs DexScreener=${dex_price:.8f} "
                                        f"({_gain_x:.0f}x entry discrepancy) — using DexScreener"
                                    )
                                    current_price = dex_price
                                    _san_confirmed = True
                                elif dex_price > 0:
                                    # DexScreener agrees price is high — genuine pump
                                    _san_confirmed = True
                            # else: no pairs — can't confirm
                        except Exception as _san_e:
                            logger.warning(f"[Trader] Sanity check fetch failed for {token_symbol}: {_san_e}")
                        if not _san_confirmed:
                            logger.error(
                                f"[Trader] ⛔ Paper sell ABORTED: {token_symbol} — "
                                f"price ${current_price:.8f} is {_gain_x:.0f}x entry "
                                f"but DexScreener could not confirm — possible feed glitch, skipping sell"
                            )
                            return
                    liquidity_usd = await self._get_token_liquidity(token_address)
                    _is_stop = any(k in reason.lower() for k in ("stop loss", "stop-loss", "dead liquidity"))
                    exit_price, usd_received, slip_est = \
                        self.paper_slippage.apply_to_sell(
                            tokens_to_sell, liquidity_usd, current_price, token_symbol,
                            is_stop_loss=_is_stop
                        )
                    impact_pct   = slip_est.total_slippage_pct
                    price_source = (
                        "pm_synced+model"
                        if _synced_price > 0 and (time.time() - _synced_ts) < 10.0
                        else "dexscreener+model"
                    )

                    cost_basis = position.entry_price_usd * tokens_to_sell
                    pnl        = usd_received - cost_basis
                    pnl_pct    = (pnl / cost_basis * 100) if cost_basis > 0 else 0

                _min_p = getattr(position, "min_price_usd", 0)
                _entry = position.entry_price_usd
                max_drawdown_pct = round((_min_p / _entry - 1) * 100, 2) if _entry > 0 and _min_p > 0 else 0.0

                if pct >= 1.0:
                    del self.open_positions[token_address]
                    self.reentry.previously_held.add(token_address.lower())
                    self.reentry.save()
                    # Unsubscribe from real-time feeds when position fully closed
                    if self._axiom_price_feed is not None:
                        self._axiom_price_feed.unsubscribe_token(token_address)
                    if self._dex_price_feed is not None:
                        self._dex_price_feed.unsubscribe_token(token_address)
                    if self._rpc_price_feed is not None:
                        self._rpc_price_feed.unsubscribe_token(token_address)
                    # Record loss cooldown for dip_buy strategy (full close, negative pnl)
                    if pnl < 0 and getattr(position, "strategy", "") == "dip_buy":
                        self._dip_loss_cooldown[token_address.lower()] = time.time()
                        self._save_dip_loss_cooldown()
                else:
                    position.amount_tokens *= (1 - pct)
                    position.amount_sol_spent *= (1 - pct)
                    position.amount_usd *= (1 - pct)

                if getattr(position, "strategy", "") != "scalp":
                    self.risk_manager.record_sell(usd_received, pnl)
                emoji = "🟢" if pnl >= 0 else "🔴"

                await self.telegram.send(
                    f"{emoji} *[PAPER] Sold ${token_symbol}* ({pct*100:.0f}%)\n\n"
                    f"💵 Received: ${usd_received:.2f}\n"
                    f"📊 PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                    f"📉 Exit impact: {impact_pct:.2f}% via {price_source}\n"
                    f"📝 {reason}"
                )
                _entry_mono = getattr(position, "entry_time_monotonic", 0)
                _hold_secs = round(time.monotonic() - _entry_mono) if _entry_mono > 0 else 0
                self.tracker.record_sell(token_address, usd_received, pnl, reason, pnl_pct=round(pnl_pct, 2), max_drawdown_pct=max_drawdown_pct, hold_secs=_hold_secs, entry_market_cap_usd=getattr(position, "entry_market_cap_usd", 0.0), entry_age_hours=getattr(position, "entry_age_hours", 0.0), entry_volume_h1_usd=getattr(position, "entry_volume_h1_usd", 0.0), pair_address=getattr(position, "pair_address", "") or "", entry_meta=getattr(position, "entry_meta", None) or {})
                logger.info(
                    f"{emoji} [PAPER] Sold {pct*100:.0f}% of {token_symbol} — "
                    f"PnL: ${pnl:+.2f} | Impact: {impact_pct:.2f}% | Source: {price_source}"
                )
                return

            # ── LIVE TRADING MODE ─────────────────────────────────────
            tokens_to_sell = int(position.amount_tokens * pct * 1e9)

            quote = await self._get_quote(
                input_mint=token_address,
                output_mint=SOL_MINT,
                amount=tokens_to_sell
            )
            if not quote:
                return

            sol_received = int(quote.get("outAmount", 0)) / 1e9
            usd_received = await self._sol_to_usd(sol_received)
            cost_basis = position.amount_usd * pct
            pnl = usd_received - cost_basis
            pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

            _min_p = getattr(position, "min_price_usd", 0)
            _entry = getattr(position, "entry_price_usd", 0)
            max_drawdown_pct = round((_min_p / _entry - 1) * 100, 2) if _entry > 0 and _min_p > 0 else 0.0

            success = await self._execute_swap(quote)
            if not success:
                return

            if pct >= 1.0:
                del self.open_positions[token_address]
                self.reentry.previously_held.add(token_address.lower())
                self.reentry.save()
                if self._axiom_price_feed is not None:
                    self._axiom_price_feed.unsubscribe_token(token_address)
                if self._dex_price_feed is not None:
                    self._dex_price_feed.unsubscribe_token(token_address)
                if self._rpc_price_feed is not None:
                    self._rpc_price_feed.unsubscribe_token(token_address)
                # Record loss cooldown for dip_buy strategy (full close, negative pnl)
                if pnl < 0 and getattr(position, "strategy", "") == "dip_buy":
                    self._dip_loss_cooldown[token_address.lower()] = time.time()
                    self._save_dip_loss_cooldown()
            else:
                position.amount_tokens *= (1 - pct)
                position.amount_sol_spent *= (1 - pct)
                position.amount_usd *= (1 - pct)

            if getattr(position, "strategy", "") != "scalp":
                self.risk_manager.record_sell(usd_received, pnl)

            emoji = "🟢" if pnl >= 0 else "🔴"
            await self.telegram.send(
                f"{emoji} *Sold ${token_symbol}* ({pct*100:.0f}%)\n\n"
                f"💵 Received: ${usd_received:.0f}\n"
                f"📊 PnL: ${pnl:+.0f} ({pnl_pct:+.1f}%)\n"
                f"📝 Reason: {reason}"
            )
            _entry_mono = getattr(position, "entry_time_monotonic", 0)
            _hold_secs = round(time.monotonic() - _entry_mono) if _entry_mono > 0 else 0
            self.tracker.record_sell(token_address, usd_received, pnl, reason, pnl_pct=round(pnl_pct, 2), max_drawdown_pct=max_drawdown_pct, hold_secs=_hold_secs, entry_market_cap_usd=getattr(position, "entry_market_cap_usd", 0.0), entry_age_hours=getattr(position, "entry_age_hours", 0.0), entry_volume_h1_usd=getattr(position, "entry_volume_h1_usd", 0.0), pair_address=getattr(position, "pair_address", "") or "", entry_meta=getattr(position, "entry_meta", None) or {})
            logger.info(f"{emoji} Sold {pct*100:.0f}% of {token_symbol} — PnL: ${pnl:+.0f}")

        except Exception as e:
            logger.error(f"Sell failed for {token_symbol}: {e}")
        finally:
            self._selling.discard(token_address)

    async def _monitor_positions(self):
        """Continuously monitor open positions for TP/SL triggers."""
        await asyncio.sleep(30)  # Wait for first positions to open
        while True:
            try:
                for token_address, position in list(self.open_positions.items()):
                    await self._check_position(position)
            except Exception as e:
                logger.error(f"Position monitor error: {e}")
            await asyncio.sleep(30)

    async def _check_position(self, position: Position):
        """Check if a position has hit take profit or stop loss."""
        current_price = await self._get_token_price(position.token_address)
        if current_price <= 0:
            return

        position.current_price_usd = current_price
        multiplier = current_price / position.entry_price_usd if position.entry_price_usd > 0 else 1
        position.pnl_usd = (multiplier - 1) * position.amount_usd

        # Stop loss
        if multiplier <= (1 - self.stop_loss_pct):
            logger.warning(f"🛑 Stop loss hit for {position.token_symbol}")
            await self.sell(position.token_address, position.token_symbol,
                          f"Stop loss at {(multiplier-1)*100:.1f}%", pct=1.0)
            return

        # Take profit 1 (2x) — sell 50%
        if multiplier >= self.tp1_multiplier and not position.take_profit_1_hit:
            position.take_profit_1_hit = True
            await self.sell(position.token_address, position.token_symbol,
                          f"TP1 at {multiplier:.1f}x", pct=0.50)

        # Take profit 2 (5x) — sell 30% of original (60% of remaining)
        elif multiplier >= self.tp2_multiplier and not position.take_profit_2_hit:
            position.take_profit_2_hit = True
            await self.sell(position.token_address, position.token_symbol,
                          f"TP2 at {multiplier:.1f}x", pct=0.60)

        # Take profit 3 (10x) — sell everything remaining
        elif multiplier >= self.tp3_multiplier:
            await self.sell(position.token_address, position.token_symbol,
                          f"TP3 at {multiplier:.1f}x", pct=1.0)

    async def _get_quote(self, input_mint: str, output_mint: str, amount: int) -> Optional[dict]:
        """Get a swap quote from Jupiter, with retries for transient DNS/network errors."""
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": 100  # 1% slippage
        }
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(headers=_JUPITER_HEADERS) as session:
                    async with session.get(JUPITER_QUOTE_API, params=params,
                                           timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        logger.warning(f"Jupiter quote HTTP {resp.status} (attempt {attempt+1}/3)")
            except Exception as e:
                logger.warning(f"Jupiter quote error (attempt {attempt+1}/3): {e}")
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s backoff
        return None

    async def _execute_swap(self, quote: dict) -> bool:
        """Execute a swap using Jupiter."""
        if not self.private_key:
            logger.warning("No private key set — skipping actual swap (paper trading mode)")
            return True  # Paper trading mode

        try:
            async with aiohttp.ClientSession(headers=_JUPITER_HEADERS) as session:
                payload = {
                    "quoteResponse": quote,
                    "userPublicKey": self._get_public_key(),
                    "wrapAndUnwrapSol": True,
                    "prioritizationFeeLamports": 10000
                }
                async with session.post(JUPITER_SWAP_API, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return False
                    swap_data = await resp.json()
                    swap_tx = swap_data.get("swapTransaction", "")
                    return await self._send_transaction(swap_tx)
        except Exception as e:
            logger.error(f"Swap execution error: {e}")
            return False

    async def _send_transaction(self, swap_tx_b64: str) -> bool:
        """Send a signed transaction to the Solana network."""
        try:
            from solders.keypair import Keypair
            from solders.transaction import VersionedTransaction
            import base58

            keypair = Keypair.from_base58_string(self.private_key)
            tx_bytes = base64.b64decode(swap_tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)
            tx.sign([keypair])

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base64.b64encode(bytes(tx)).decode("utf-8"),
                    {"encoding": "base64", "skipPreflight": False}
                ]
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(self.rpc_url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    result = await resp.json()
                    if "error" in result:
                        logger.error(f"TX error: {result['error']}")
                        return False
                    logger.info(f"TX sent: {result.get('result', '')}")
                    return True
        except ImportError:
            logger.warning("solders not installed — run: pip install solders")
            return False
        except Exception as e:
            logger.error(f"Transaction error: {e}")
            return False

    def _get_public_key(self) -> str:
        """Derive public key from private key."""
        try:
            from solders.keypair import Keypair
            keypair = Keypair.from_base58_string(self.private_key)
            return str(keypair.pubkey())
        except Exception:
            return ""

    async def _get_token_liquidity(self, token_address: str) -> float:
        """Get token pool liquidity in USD from DexScreener."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    data = await resp.json(content_type=None)
                    pairs = data.get("pairs", [])
                    if pairs:
                        return float(
                            pairs[0].get("liquidity", {}).get("usd", 0) or 0
                        )
        except Exception:
            pass
        return 50_000  # Fallback if unavailable

    async def _get_token_price(self, token_address: str) -> float:
        """Get current token price in USD — tries Axiom cache, Axiom REST, Jupiter, DexScreener."""
        # 0. Axiom real-time price cache (WebSocket, ~1s latency) — skip SOL_MINT
        if self._axiom_price_feed is not None and token_address != SOL_MINT:
            cached_price = self._axiom_price_feed.price_cache.get(token_address, 0)
            cached_ts    = self._axiom_price_feed.price_timestamps.get(token_address, 0)
            if cached_price > 0 and (time.time() - cached_ts) < 5.0:
                logger.debug(f"[Trader] Axiom cache hit: {token_address[:8]} = ${cached_price:.8f}")
                return cached_price

        # 1. Axiom token info (most reliable for tokens the bot trades)
        if self._axiom_auth is not None:
            try:
                client = self._axiom_auth.get_client()
                if client:
                    loop = asyncio.get_event_loop()
                    info = await loop.run_in_executor(None, client.get_token_info, token_address)
                    price = float(
                        info.get("priceUsd") or info.get("price_usd") or
                        info.get("price") or 0
                    )
                    if price > 0:
                        return price
            except Exception as e:
                logger.debug(f"[Trader] Axiom price lookup failed for {token_address[:8]}: {e}")
        # 2. Jupiter price API v6
        try:
            url = f"https://price.jup.ag/v6/price?ids={token_address}&vsToken=USDC"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json(content_type=None)
                    price = data.get("data", {}).get(token_address, {}).get("price", 0)
                    if price and price > 0:
                        return float(price)
        except Exception:
            pass
        # DexScreener fallback (captures new pump.fun tokens not yet on Jupiter)
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json(content_type=None)
                    pairs = data.get("pairs") or []
                    if pairs:
                        # Prefer graduated DEX pools over PumpFun pre-graduation pool
                        _grad = [p for p in pairs if p.get("dexId", "") != "pump-fun" and p.get("liquidity", {}).get("usd", 0) > 1000]
                        best = max(_grad or pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0))
                        price = float(best.get("priceUsd", 0) or 0)
                        if price > 0:
                            return price
        except Exception as e:
            logger.debug(f"[Trader] DexScreener price fallback failed for {token_address[:8]}: {e}")
        return 0

    async def _usd_to_sol(self, usd_amount: float) -> float:
        """Convert USD amount to SOL."""
        sol_price = await self._get_token_price(SOL_MINT)
        return usd_amount / sol_price if sol_price > 0 else 0

    async def _sol_to_usd(self, sol_amount: float) -> float:
        """Convert SOL amount to USD."""
        sol_price = await self._get_token_price(SOL_MINT)
        return sol_amount * sol_price
