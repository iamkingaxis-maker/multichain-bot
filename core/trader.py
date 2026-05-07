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
from typing import Dict, List, Optional, Set
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
    # Batch 2 (2026-04-25): max favorable excursion during hold.  Updated by
    # PositionManager._apply_price_update on every price tick.  Logged at sell
    # to expose how much "ceiling" we left on the table per trade.
    peak_pnl_pct: float = 0.0
    peak_pnl_at_secs: int = 0  # seconds from entry when peak occurred
    # Live order-flow ratios — updated by PositionManager._poll_dexscreener
    # on each poll so we can capture the bs_h1/bs_m5 state at exit time.
    # 0.0 = no data yet; values are capped at 999.0 to avoid +inf serialization.
    current_bs_h1: float = 0.0
    current_bs_m5: float = 0.0
    # Mint decimals — fetched at buy time, used for atomic-unit math at sell time.
    # pump.fun tokens are 6 decimals, most SPL tokens are 9.  Hardcoding 1e9 caused
    # a 1000× off-by-decimals bug on the first live buy (TripleT 2026-04-28).
    token_decimals: int = 6
    # Hold-time pnl snapshots — populated by PositionManager when age crosses
    # 30/60/90/120 min thresholds. Used for "stale exit" calibration: 12-day
    # history shows trades held >60min are net −$1090 in aggregate, but we
    # need per-trade trajectory data to design a conditional exit safely.
    hold_pnl_snapshots: Optional[dict] = None  # {"30m": -2.1, "60m": +0.3, ...}
    # Mid-hold top10_holder_pct snapshots — sampled at the same age thresholds
    # as hold_pnl_snapshots. Lets us measure distribution velocity (entry ->
    # 30m -> 60m -> ... -> exit) instead of just entry-to-exit delta.
    holder_snapshots: Optional[dict] = None    # {"30m": 14.2, "60m": 13.8, ...}
    # Mid-hold rich snapshots (added 2026-05-02) — sampled at the same 30/60/
    # 90/120m thresholds. lp_snapshots: imbalance ratio + dominant pool depth
    # from rugcheck markets array (LP draining = pre-rug signal). rugcheck
    # _score_snapshots: score_normalised drift during hold. orderflow_
    # snapshots: bs_m5 / bs_h1 / pc_m5 / pc_h1 / vol_m5 / vol_h1 from
    # DexScreener — answers "did the order flow invert before the dump?"
    lp_snapshots: Optional[dict] = None        # {"30m": {"imbalance": 1.0, "depth_usd": 240000}, ...}
    rugcheck_score_snapshots: Optional[dict] = None  # {"30m": 50.0, ...}
    orderflow_snapshots: Optional[dict] = None # {"30m": {"bs_m5": 1.2, "bs_h1": 1.5, "pc_m5": -2.1, "pc_h1": +6.0, "vol_m5": 5000, "vol_h1": 80000}, ...}


_DATA_DIR = os.environ.get("DATA_DIR", ".")
_REENTRY_STATE_FILE = os.path.join(_DATA_DIR, "reentry_state.json")
_OPEN_POSITIONS_FILE = os.path.join(_DATA_DIR, "open_positions.json")


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
        self.rpc_url = rpc_url  # primary, kept as string for back-compat with other modules
        # Multi-RPC failover list — primary plus optional backups from env
        # SOLANA_RPC_URL_BACKUP (comma-separated for multiple).  Failover keeps
        # live trading alive when the primary has an outage or rate-limits us.
        _backup_env = os.environ.get("SOLANA_RPC_URL_BACKUP", "").strip()
        backups = [u.strip() for u in _backup_env.split(",") if u.strip()] if _backup_env else []
        self.rpc_urls: list = [rpc_url] + [u for u in backups if u != rpc_url]
        if len(self.rpc_urls) > 1:
            logger.info(f"[Trader] {len(self.rpc_urls)} RPC endpoints configured (primary + {len(self.rpc_urls)-1} backup)")
        # Priority fee config — Jupiter accepts an "auto"-style object that
        # adapts to current network congestion.  Cap via env to bound per-tx
        # cost (default 1M lamports = 0.001 SOL ≈ $0.10).
        self._max_priority_lamports = int(os.environ.get("MAX_PRIORITY_LAMPORTS", "1000000"))
        self._priority_level = os.environ.get("PRIORITY_LEVEL", "high")  # medium|high|veryHigh

        # SOL gas reserve — block live trades when wallet SOL balance falls below
        # this threshold.  0.05 SOL ≈ 50 priority-fee budgets at 0.001 SOL each.
        self._min_sol_reserve = float(os.environ.get("MIN_SOL_RESERVE", "0.05"))
        # Cached SOL balance — refreshed every _sol_balance_ttl seconds.
        self._sol_balance: float = -1.0  # -1 = never queried
        self._sol_balance_ts: float = 0.0
        self._sol_balance_ttl: float = 30.0

        # Execution stats — counts surfacing live-mode swap reliability via /api/stats.
        # Updated by _get_quote, _execute_swap, _await_tx_confirmation.
        self._exec_stats: Dict[str, int] = {
            "swaps_attempted":   0,  # buy or sell live attempts (per try, not per position)
            "quote_failures":    0,  # _get_quote returned None after retries within one call
            "swap_failures":     0,  # _execute_swap returned False (non-200, exception, or tx error)
            "confirm_timeouts":  0,  # tx accepted but never confirmed within 45s
            "confirm_errors":    0,  # tx confirmed with on-chain error (slippage exceeded, compute, etc.)
            "successful_swaps":  0,  # tx confirmed successfully on-chain
            "blocked_low_sol":   0,  # buys blocked because SOL balance < reserve
        }
        # Realized slippage (live mode): set by _execute_swap after balance-delta calc.
        # Read by buy/sell paths after successful swap; reset on every attempt.
        self._last_realized_slippage_pct: float = 0.0
        self._realized_slippage_history: list = []  # rolling window for avg

        # Dashboard pause flag — set by /api/pause on the dashboard.  Lives in
        # memory (no env round-trip), so toggles take effect immediately.
        # Buy gate ORs this with the env-based TRADING_PAUSED flag.
        self._dashboard_paused: bool = False
        self.tracker = tracker
        self.telegram = telegram
        self.risk_manager = risk_manager
        self.kill_switch = kill_switch
        self.open_positions: Dict[str, Position] = {}
        self.session: Optional[aiohttp.ClientSession] = None

        # Take profit levels (from config)
        # TP/SL is fully owned by PositionManager — these legacy fields
        # were referenced by the deleted _monitor_positions / _check_position
        # zombie methods. Keep stop_loss_pct only because it's still in the
        # __init__ signature (callers may pass it positionally).
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

        # Restore live open_positions from disk so a Railway redeploy doesn't
        # lose track of in-flight on-chain holdings.  No-op in paper mode.
        # Followed by reconcile_positions_on_startup which validates each
        # restored position against the on-chain wallet balance.
        self._restore_open_positions()

        # Sync risk_manager.available_capital with the actual restored
        # positions. RiskManager._load_state historically reclaimed deployed
        # capital on restart (assuming positions didn't survive); now that
        # they do, that reclaim double-counts. Reconcile here so the next
        # buy decision sees the correct free capital. Re-run after
        # reconcile_positions_on_startup (which may prune ghosts).
        if self.risk_manager and hasattr(self.risk_manager, "reconcile_with_open_positions"):
            try:
                self.risk_manager.reconcile_with_open_positions(self.open_positions)
            except Exception as e:
                logger.warning(f"[Trader] post-restore risk reconcile failed: {e}")

    async def _post_rpc(self, payload: dict, total_timeout: float = 10.0) -> Optional[dict]:
        """
        POST a JSON-RPC payload, trying each configured RPC URL until one
        responds successfully.  Returns parsed JSON on first success, or
        None if all endpoints fail.  Used by tx send, confirmation polling,
        and reconciliation.
        """
        for idx, url in enumerate(self.rpc_urls):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload,
                                            timeout=aiohttp.ClientTimeout(total=total_timeout)) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        logger.warning(
                            f"[Trader] RPC {idx} HTTP {resp.status} for "
                            f"{payload.get('method','?')}, trying next..."
                        )
            except Exception as e:
                logger.warning(
                    f"[Trader] RPC {idx} error for {payload.get('method','?')}: "
                    f"{type(e).__name__} — trying next..."
                )
        return None

    async def _get_sol_balance(self, force: bool = False) -> float:
        """
        Query wallet SOL balance via getBalance RPC.  Cached for _sol_balance_ttl
        seconds to avoid hammering RPC.  Returns balance in SOL (float), or -1.0
        on RPC failure.  Skipped in paper mode (returns 0.0).
        """
        if not self.private_key:
            return 0.0
        if not force and self._sol_balance >= 0 and (time.time() - self._sol_balance_ts) < self._sol_balance_ttl:
            return self._sol_balance
        owner = self._get_public_key()
        if not owner:
            return -1.0
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [owner],
        }
        data = await self._post_rpc(payload, total_timeout=5.0) or {}
        lamports = (data.get("result") or {}).get("value", -1)
        if lamports < 0:
            return -1.0
        sol = lamports / 1e9
        self._sol_balance = sol
        self._sol_balance_ts = time.time()
        return sol

    async def _check_sol_reserve(self, token_symbol: str = "?") -> bool:
        """
        Live-mode pre-trade gate: ensure wallet SOL >= _min_sol_reserve.
        Returns True if OK to trade, False if balance below reserve (blocks trade).
        Paper mode bypasses (returns True).  RPC failure also bypasses (returns
        True with a warning) so an unrelated outage doesn't halt all trading —
        the swap itself will fail loudly if the wallet truly is empty.
        """
        if not self.private_key:
            return True
        sol = await self._get_sol_balance()
        if sol < 0:
            logger.warning(
                f"[Trader] SOL reserve check: getBalance failed for {token_symbol} — "
                f"allowing trade (swap will fail if wallet is actually empty)"
            )
            return True
        if sol < self._min_sol_reserve:
            self._exec_stats["blocked_low_sol"] += 1
            logger.error(
                f"[Trader] ⛔ Trade blocked: wallet SOL {sol:.4f} < reserve "
                f"{self._min_sol_reserve:.4f} — top up wallet to resume live trading"
            )
            try:
                await self.telegram.send(
                    f"⛔ *Trade blocked — low SOL*\n\n"
                    f"Wallet: {sol:.4f} SOL\n"
                    f"Reserve: {self._min_sol_reserve:.4f} SOL\n"
                    f"Top up to resume live trading."
                )
            except Exception:
                pass
            return False
        return True

    def _save_open_positions(self) -> None:
        """
        Atomically persist open_positions to /data/open_positions.json.
        Live-only: skipped in paper mode (paper positions are deliberately
        ephemeral so deploys reset clean).  Called after every buy/sell
        mutation so a Railway redeploy mid-flight doesn't lose state.
        """
        if not self.private_key:
            return  # paper mode — keep ephemeral
        try:
            payload = {"positions": []}
            for addr, p in self.open_positions.items():
                payload["positions"].append({
                    "token_address": p.token_address,
                    "token_symbol": p.token_symbol,
                    "entry_price_usd": p.entry_price_usd,
                    "amount_tokens": p.amount_tokens,
                    "amount_sol_spent": p.amount_sol_spent,
                    "entry_time": p.entry_time.isoformat() if p.entry_time else None,
                    "reason": p.reason,
                    "take_profit_1_hit": p.take_profit_1_hit,
                    "take_profit_2_hit": p.take_profit_2_hit,
                    "current_price_usd": p.current_price_usd,
                    "signal_score": p.signal_score,
                    "hh_hl_confirmed": p.hh_hl_confirmed,
                    "chain_id": p.chain_id,
                    "amount_usd": p.amount_usd,
                    "strategy": p.strategy,
                    "pair_address": p.pair_address,
                    "min_price_usd": p.min_price_usd,
                    "entry_market_cap_usd": p.entry_market_cap_usd,
                    "entry_age_hours": p.entry_age_hours,
                    "entry_volume_h1_usd": p.entry_volume_h1_usd,
                    "scalp_meta": p.scalp_meta,
                    "entry_meta": p.entry_meta,
                    "peak_pnl_pct": p.peak_pnl_pct,
                    "peak_pnl_at_secs": p.peak_pnl_at_secs,
                    "token_decimals": p.token_decimals,
                    "hold_pnl_snapshots": p.hold_pnl_snapshots or {},
                    "holder_snapshots": p.holder_snapshots or {},
                    "lp_snapshots": p.lp_snapshots or {},
                    "rugcheck_score_snapshots": p.rugcheck_score_snapshots or {},
                    "orderflow_snapshots": p.orderflow_snapshots or {},
                })
            tmp = _OPEN_POSITIONS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, _OPEN_POSITIONS_FILE)
        except Exception as e:
            logger.warning(f"[Trader] _save_open_positions failed: {e}")

    def _restore_open_positions(self) -> None:
        """
        Restore open_positions from disk on startup.  Live-only.  Paired with
        reconcile_positions_on_startup which then validates each restored
        position against actual on-chain wallet holdings.
        """
        if not self.private_key:
            return
        if not os.path.exists(_OPEN_POSITIONS_FILE):
            logger.info("[Trader] No persisted open_positions to restore")
            return
        try:
            with open(_OPEN_POSITIONS_FILE) as f:
                payload = json.load(f)
            for d in payload.get("positions", []):
                try:
                    et = datetime.fromisoformat(d.get("entry_time")) if d.get("entry_time") else datetime.now(timezone.utc)
                except Exception:
                    et = datetime.now(timezone.utc)
                p = Position(
                    token_address=d["token_address"],
                    token_symbol=d.get("token_symbol", "?"),
                    entry_price_usd=float(d.get("entry_price_usd", 0.0)),
                    amount_tokens=float(d.get("amount_tokens", 0.0)),
                    amount_sol_spent=float(d.get("amount_sol_spent", 0.0)),
                    entry_time=et,
                    reason=d.get("reason", "restored"),
                    take_profit_1_hit=bool(d.get("take_profit_1_hit", False)),
                    take_profit_2_hit=bool(d.get("take_profit_2_hit", False)),
                    current_price_usd=float(d.get("current_price_usd", 0.0)),
                    signal_score=int(d.get("signal_score", 0)),
                    hh_hl_confirmed=bool(d.get("hh_hl_confirmed", False)),
                    chain_id=d.get("chain_id", "solana"),
                    amount_usd=float(d.get("amount_usd", 0.0)),
                    strategy=d.get("strategy", "scanner"),
                    pair_address=d.get("pair_address", ""),
                    min_price_usd=float(d.get("min_price_usd", 0.0)),
                    entry_time_monotonic=time.monotonic(),  # reset — best we can do
                    entry_market_cap_usd=float(d.get("entry_market_cap_usd", 0.0)),
                    entry_age_hours=float(d.get("entry_age_hours", 0.0)),
                    entry_volume_h1_usd=float(d.get("entry_volume_h1_usd", 0.0)),
                    scalp_meta=d.get("scalp_meta"),
                    entry_meta=d.get("entry_meta"),
                    peak_pnl_pct=float(d.get("peak_pnl_pct", 0.0)),
                    peak_pnl_at_secs=int(d.get("peak_pnl_at_secs", 0)),
                    token_decimals=int(d.get("token_decimals", 6)),
                    hold_pnl_snapshots=dict(d.get("hold_pnl_snapshots") or {}),
                    holder_snapshots=dict(d.get("holder_snapshots") or {}),
                    lp_snapshots=dict(d.get("lp_snapshots") or {}),
                    rugcheck_score_snapshots=dict(d.get("rugcheck_score_snapshots") or {}),
                    orderflow_snapshots=dict(d.get("orderflow_snapshots") or {}),
                )
                # Dict keys are always lowercased; Position.token_address keeps
                # the original-case mint for Jupiter/RPC calls.
                self.open_positions[p.token_address.lower()] = p
            # Drop dust positions: amount_usd < $1 means a TP-bug or partial-sell
            # residue. Cheaper to abandon than to swap for fractions of a cent.
            _dust = [k for k, p in self.open_positions.items()
                     if float(getattr(p, "amount_usd", 0) or 0) < 1.0]
            for k in _dust:
                _p = self.open_positions[k]
                logger.warning(
                    f"[Trader] Dust cleanup: dropping {_p.token_symbol} "
                    f"(${float(getattr(_p,'amount_usd',0) or 0):.6f})"
                )
                del self.open_positions[k]
            if _dust:
                self._save_open_positions()
            logger.info(f"[Trader] Restored {len(self.open_positions)} open positions from disk")
        except Exception as e:
            logger.warning(f"[Trader] _restore_open_positions failed: {e}")

    async def _get_token_decimals(self, mint: str) -> int:
        """
        Query mint decimals via getAccountInfo (parsed).  Returns the integer
        decimals, falls back to 6 (pump.fun convention) on RPC failure since
        most tokens we trade are pump.fun.  Used by live buy to record the
        correct atomic-unit divisor on Position.token_decimals.
        """
        if not mint:
            return 6
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [mint, {"encoding": "jsonParsed"}],
        }
        try:
            data = await self._post_rpc(payload, total_timeout=5.0) or {}
            info = ((data.get("result") or {}).get("value") or {}).get("data", {})
            parsed = info.get("parsed", {}).get("info", {}) if isinstance(info, dict) else {}
            decimals = parsed.get("decimals")
            if isinstance(decimals, int) and 0 <= decimals <= 18:
                return decimals
        except Exception as e:
            logger.debug(f"[Trader] _get_token_decimals failed for {mint[:8]}…: {e}")
        return 6  # fallback: pump.fun is the most common case

    async def capture_holder_snapshot(self, token_address: str, label: str) -> None:
        """
        Sample mid-hold security state — top10_holder_pct, rugcheck_score,
        and LP imbalance — into parallel snapshot dicts on the open Position.
        Called as a fire-and-forget task from position_manager at 30/60/90/
        120-min hold thresholds. One rugcheck call covers all three (gaps 2
        and 4 closed for free alongside the existing holder snapshot).

        Fail-soft on every step — analytics nice-to-have, never affects trading.
        """
        try:
            position = self.open_positions.get((token_address or "").lower())
            if position is None or self._security_checker is None:
                return
            if position.holder_snapshots and label in position.holder_snapshots:
                return  # already sampled this threshold
            _rc_full = await self._security_checker._fetch_rugcheck_full(token_address)
            if not isinstance(_rc_full, dict):
                return

            # holder snapshot (existing)
            _LP_TAGS = {"lp", "liquidity", "liquiditypool", "pool", "amm", "bonding curve"}
            th = _rc_full.get("topHolders") or []
            if isinstance(th, list) and th:
                real = [
                    h for h in th
                    if isinstance(h, dict)
                    and h.get("insider", False) is not True
                    and (h.get("tag", "") or "").lower().strip() not in _LP_TAGS
                ]
                top10 = sum(float(h.get("pct", 0) or 0) for h in real[:10])
                if position.holder_snapshots is None:
                    position.holder_snapshots = {}
                position.holder_snapshots[label] = round(top10, 2)

            # rugcheck score snapshot (gap 4)
            try:
                _score = _rc_full.get("score_normalised")
                if _score is not None:
                    if position.rugcheck_score_snapshots is None:
                        position.rugcheck_score_snapshots = {}
                    position.rugcheck_score_snapshots[label] = round(float(_score), 2)
            except Exception:
                pass

            # LP imbalance snapshot (gap 2) — same parsing as buy-time meta
            try:
                _markets = _rc_full.get("markets") or []
                if isinstance(_markets, list) and _markets:
                    _best_lp = None
                    _best_depth = -1.0
                    for _m in _markets:
                        if not isinstance(_m, dict):
                            continue
                        _lp = _m.get("lp") or {}
                        if not isinstance(_lp, dict):
                            continue
                        _b = float(_lp.get("baseUSD") or 0)
                        _q = float(_lp.get("quoteUSD") or 0)
                        _depth = _b + _q
                        if _depth > _best_depth:
                            _best_depth = _depth
                            _best_lp = (_b, _q)
                    if _best_lp and _best_depth > 0:
                        _b, _q = _best_lp
                        _hi = max(_b, _q)
                        _lo = max(min(_b, _q), 0.01)
                        _ratio = _hi / _lo
                        if position.lp_snapshots is None:
                            position.lp_snapshots = {}
                        position.lp_snapshots[label] = {
                            "imbalance": round(_ratio, 3),
                            "depth_usd": round(_best_depth, 2),
                        }
            except Exception:
                pass

            _hs = (position.holder_snapshots or {}).get(label)
            _ls = (position.lp_snapshots or {}).get(label) or {}
            _rs = (position.rugcheck_score_snapshots or {}).get(label)
            logger.info(
                f"[Trader] HOLD SNAPSHOT: {position.token_symbol} @ {label} "
                f"top10={_hs}% score={_rs} lp_imbalance={_ls.get('imbalance')} "
                f"lp_depth=${_ls.get('depth_usd')}"
            )
        except Exception as _e:
            logger.debug(f"[Trader] hold snapshot failed: {_e}")

    async def capture_orderflow_snapshot(self, token_address: str, label: str) -> None:
        """
        Sample mid-hold order flow + momentum from DexScreener (gap 1). Single
        DS call per threshold; bs_m5 / bs_h1 / pc_m5 / pc_h1 / vol_m5 / vol_h1
        captured into orderflow_snapshots[label]. Answers "did order flow
        invert before the dump?" — bs_m5 trajectory is the direct signal,
        the rest is context.

        Called as a fire-and-forget task from position_manager. Fail-soft.
        """
        try:
            position = self.open_positions.get((token_address or "").lower())
            if position is None:
                return
            if position.orderflow_snapshots and label in position.orderflow_snapshots:
                return
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status != 200:
                            return
                        data = await r.json()
            except Exception:
                return
            pairs = data.get("pairs") or []
            sol = [p for p in pairs if p.get("chainId") == "solana"]
            if not sol:
                return
            # Use the same pool we entered through if possible, else max liquidity
            pool = next(
                (p for p in sol if (p.get("pairAddress") or "").lower()
                 == (getattr(position, "pair_address", "") or "").lower()),
                None,
            ) or max(sol, key=lambda x: (x.get("liquidity") or {}).get("usd", 0) or 0)
            txns = pool.get("txns") or {}
            pc = pool.get("priceChange") or {}
            vol = pool.get("volume") or {}

            def _ratio(t):
                b = (t or {}).get("buys") or 0
                s = (t or {}).get("sells") or 0
                if not s:
                    return None  # avoid +inf serialization
                return round(float(b) / float(s), 3)

            snapshot = {
                "bs_m5": _ratio(txns.get("m5")),
                "bs_h1": _ratio(txns.get("h1")),
                "bs_h6": _ratio(txns.get("h6")),
                "pc_m5": float(pc.get("m5") or 0),
                "pc_h1": float(pc.get("h1") or 0),
                "vol_m5": float(vol.get("m5") or 0),
                "vol_h1": float(vol.get("h1") or 0),
                "liq_usd": float((pool.get("liquidity") or {}).get("usd") or 0),
            }
            if position.orderflow_snapshots is None:
                position.orderflow_snapshots = {}
            position.orderflow_snapshots[label] = snapshot
            logger.info(
                f"[Trader] ORDERFLOW SNAPSHOT: {position.token_symbol} @ {label} "
                f"bs_m5={snapshot['bs_m5']} pc_m5={snapshot['pc_m5']:+.2f}% "
                f"pc_h1={snapshot['pc_h1']:+.2f}% liq=${snapshot['liq_usd']/1e3:.0f}k"
            )
        except Exception as _e:
            logger.debug(f"[Trader] orderflow snapshot failed: {_e}")

    async def _snapshot_sell_time_meta(self, position) -> Dict[str, float]:
        """
        Capture sell-time analytics that the original entry_meta couldn't have:
        a fresh top10_holder_pct snapshot and the delta vs entry. Called at
        sell time. Fail-soft (returns empty dict on any error).
        """
        out: Dict[str, float] = {}
        try:
            mint = getattr(position, "token_address", "") or ""
            if not mint or self._security_checker is None:
                return out
            _rc_full = await self._security_checker._fetch_rugcheck_full(mint)
            if not isinstance(_rc_full, dict):
                return out
            _LP_TAGS = {"lp", "liquidity", "liquiditypool", "pool", "amm", "bonding curve"}
            th = _rc_full.get("topHolders") or []
            if isinstance(th, list) and th:
                real = [
                    h for h in th
                    if isinstance(h, dict)
                    and h.get("insider", False) is not True
                    and (h.get("tag", "") or "").lower().strip() not in _LP_TAGS
                ]
                top10_now = sum(float(h.get("pct", 0) or 0) for h in real[:10])
                out["top10_holder_pct_at_sell"] = round(top10_now, 2)
                em = getattr(position, "entry_meta", None) or {}
                top10_buy = em.get("top10_holder_pct")
                if top10_buy is not None:
                    out["top10_holder_delta"] = round(top10_now - float(top10_buy), 2)
            # Include the mid-hold trajectory so analysis can compute velocity.
            snaps = getattr(position, "holder_snapshots", None) or {}
            if snaps:
                out["holder_snapshots"] = dict(snaps)
            # Mid-hold rich snapshots (added 2026-05-02 alongside gaps 1+2+4):
            # LP imbalance trajectory, rugcheck score drift, and order-flow
            # trajectory. All keyed by the same 30/60/90/120m labels as
            # hold_pnl_snapshots and holder_snapshots.
            lps = getattr(position, "lp_snapshots", None) or {}
            if lps:
                out["lp_snapshots"] = dict(lps)
            rss = getattr(position, "rugcheck_score_snapshots", None) or {}
            if rss:
                out["rugcheck_score_snapshots"] = dict(rss)
            ofs = getattr(position, "orderflow_snapshots", None) or {}
            if ofs:
                out["orderflow_snapshots"] = dict(ofs)
        except Exception:
            pass
        return out

    async def _get_token_balance_atomic(self, mint: str) -> int:
        """
        Query wallet's atomic-units balance of a given SPL token mint.  Returns
        the integer amount (decimals as-is from the chain), or 0 if no account
        exists.  Returns -1 on RPC failure.

        Uses mint-filter (not programId-filter) so it works for both classic
        SPL Token and Token-2022 mints without dispatching twice.
        """
        if not self.private_key:
            return 0
        owner = self._get_public_key()
        if not owner:
            return -1
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                owner,
                {"mint": mint},
                {"encoding": "jsonParsed"},
            ],
        }
        data = await self._post_rpc(payload, total_timeout=5.0) or {}
        accounts = (data.get("result") or {}).get("value") or []
        if not accounts:
            return 0
        try:
            total = 0
            for acct in accounts:
                info = (acct.get("account", {}).get("data", {}).get("parsed", {})
                        .get("info", {}))
                amount_str = (info.get("tokenAmount", {}) or {}).get("amount", "0")
                total += int(amount_str)
            return total
        except Exception:
            return -1

    def get_execution_stats(self) -> dict:
        """
        Return snapshot of live-mode execution counters plus realized-slippage
        rolling stats.  Surfaced via /api/stats.
        """
        history = self._realized_slippage_history
        avg_realized = round(sum(history) / len(history), 3) if history else 0.0
        max_realized = round(max(history), 3) if history else 0.0
        attempts = self._exec_stats["swaps_attempted"]
        successes = self._exec_stats["successful_swaps"]
        success_rate = round(successes / attempts * 100, 1) if attempts > 0 else 0.0
        return {
            **self._exec_stats,
            "success_rate_pct":    success_rate,
            "avg_realized_slippage_pct":  avg_realized,
            "max_realized_slippage_pct":  max_realized,
            "realized_samples":    len(history),
            "min_sol_reserve":     self._min_sol_reserve,
            "wallet_sol_balance":  round(self._sol_balance, 4) if self._sol_balance >= 0 else None,
        }

    async def reconcile_positions_on_startup(self) -> None:
        """
        Live-mode startup reconciliation: for each position in open_positions,
        verify the wallet actually holds the expected token.  If on-chain
        balance is zero, the position is a ghost (sold during downtime via
        another path, or a failed-then-succeeded swap left no tokens).  Mark
        it closed with a synthetic sell at last-known price so the bot doesn't
        try to sell tokens we no longer have.

        Skipped in paper mode (no wallet to query).
        """
        if not self.private_key:
            return  # paper mode — no wallet
        if not self.open_positions:
            logger.info("[Trader] reconcile_positions: no open positions to check")
            return
        try:
            owner = self._get_public_key()
            if not owner:
                logger.warning("[Trader] reconcile_positions: could not derive public key")
                return
            # Query BOTH SPL Token programs.  Pump.fun-graduated tokens live
            # in Token-2022; classic SPL Token is for older mints.  Querying
            # only one missed the entire pump.fun universe and made every
            # pump.fun position look like a ghost on restart (bug 2026-04-28).
            on_chain_balances: Dict[str, float] = {}
            for program_id in (
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # classic SPL
                "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022 (pump.fun)
            ):
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        owner,
                        {"programId": program_id},
                        {"encoding": "jsonParsed"},
                    ],
                }
                data = await self._post_rpc(payload, total_timeout=15.0) or {}
                accounts = (data.get("result") or {}).get("value") or []
                for acct in accounts:
                    info = ((acct.get("account") or {}).get("data") or {}).get("parsed") or {}
                    mint = (info.get("info") or {}).get("mint", "").lower()
                    amt = ((info.get("info") or {}).get("tokenAmount") or {}).get("uiAmount") or 0
                    if mint and amt:
                        # Aggregate — same mint may have accounts under both programs in edge cases
                        on_chain_balances[mint] = on_chain_balances.get(mint, 0.0) + float(amt)
            ghosts: list = []
            for addr, pos in list(self.open_positions.items()):
                expected = float(getattr(pos, "amount_tokens", 0) or 0)
                actual = on_chain_balances.get(addr.lower(), 0)
                if actual <= max(0.001, expected * 0.01):  # less than 1% of expected → ghost
                    ghosts.append((addr, pos, actual, expected))
            if not ghosts:
                logger.info(
                    f"[Trader] reconcile_positions: {len(self.open_positions)} positions "
                    f"verified on-chain"
                )
                return
            logger.warning(
                f"[Trader] reconcile_positions: {len(ghosts)} ghost positions detected "
                f"(in DB but not in wallet)"
            )
            for addr, pos, actual, expected in ghosts:
                logger.warning(
                    f"  → {pos.token_symbol} ({addr[:8]}…): expected {expected:.4f} tokens, "
                    f"on-chain {actual:.4f} — closing as orphan"
                )
                # Synthetic sell at entry price (no real fill data — best-effort).
                _entry_mono = getattr(pos, "entry_time_monotonic", 0)
                _hold_secs = round(time.monotonic() - _entry_mono) if _entry_mono > 0 else 0
                self.tracker.record_sell(
                    addr, getattr(pos, "amount_usd", 0) or 0, 0.0,
                    "Orphan reconciliation on startup (position not in wallet)",
                    pnl_pct=0.0, max_drawdown_pct=0.0, hold_secs=_hold_secs,
                    pair_address=getattr(pos, "pair_address", "") or "",
                    entry_meta=getattr(pos, "entry_meta", None) or {},
                )
                del self.open_positions[addr]
                self.reentry.previously_held.add(addr.lower())
            self.reentry.save()
            self._save_open_positions()
            # Re-sync risk_manager after ghost cleanup — deployed amount
            # changed when ghosts were removed, so available_capital must
            # reflect the new reality.
            if self.risk_manager and hasattr(self.risk_manager, "reconcile_with_open_positions"):
                try:
                    self.risk_manager.reconcile_with_open_positions(self.open_positions)
                except Exception as _e:
                    logger.warning(f"[Trader] post-reconcile risk sync failed: {_e}")
        except Exception as e:
            logger.error(f"[Trader] reconcile_positions failed: {e}")

    def is_dip_in_cooldown(self, token_address: str, window_seconds: float = 1800.0) -> bool:
        """
        Return True if this token had a dip_buy close within an active cooldown
        window.  Cooldown duration is stored per-token: regular closes use
        `window_seconds` (default 30 min); volume-death closes use 6h
        (registered explicitly via `_register_dip_close`).  Wall-clock based
        so cooldowns survive process restarts.

        Storage formats supported (backward-compat):
          - float: legacy "ts only" — applies window_seconds default
          - [ts, secs]: explicit (ts, cooldown_secs)
        """
        addr = token_address.lower()
        entry = self._dip_loss_cooldown.get(addr)
        if entry is None:
            return False
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            ts, secs = float(entry[0]), float(entry[1])
        else:
            ts, secs = float(entry), float(window_seconds)
        return (time.time() - ts) < secs

    def _register_dip_close(self, token_address: str, reason: str) -> None:
        """Register a dip_buy full close.

        TP/trail/manual closes get 30-min cooldown.  Lifetime data shows
        <30min post-TP rebuys net -$471 (53% WR but heavy loser tail);
        the cooldown earns its keep here.

        Stop-loss and volume-death closes used to get 6h cooldown.
        REMOVED 2026-05-02 after gap-banded re-analysis showed:
          <30min post-stop rebuys: n=50, 70% WR, +$931.83 (winners!)
          30m-2h post-stop rebuys: n=6, 33% WR, -$123 (losers)
          2-6h post-stop rebuys:   n=12, 33% WR, -$209 (losers)
          6-24h post-stop rebuys:  n=22, 50% WR, +$232 (mixed/positive)

        The original "-$923 across 128 trades" claim that justified the
        6h cooldown averaged the bands together.  Sub-30min is the
        pump-still-active rebuy and is highly profitable.  The 30m-6h
        loser band should now be caught by corpse + fake_bounce +
        peak_floor + big-cap-exempt filter stack at entry time, since
        those are tokens that have actually structurally broken.

        Net effect of removal: +$600 lifetime, gives the bot back
        April-28-style multi-entry-on-runner volume.  Watch forward —
        if the 30m-6h loser band re-appears in live data, revisit."""
        reason_lower = (reason or "").lower()
        is_vol_death = "volume death" in reason_lower
        is_stop_loss = ("stop" in reason_lower) and ("kill" not in reason_lower)
        if is_vol_death or is_stop_loss:
            # No cooldown — trust entry filter stack to gate rebuys.
            tag = "vol-death" if is_vol_death else "stop-loss"
            logger.info(
                f"[Trader] {tag} close on {token_address[:8]}… — no cooldown "
                f"(filter stack gates rebuy)"
            )
            return
        self._dip_loss_cooldown[token_address.lower()] = [time.time(), 1800.0]
        self._save_dip_loss_cooldown()

    def _load_dip_loss_cooldown(self) -> None:
        """Load persisted cooldowns; prune entries whose deadlines have passed.

        Supports two file formats:
          - legacy `{addr: ts}` — each entry treated as a 30-min cooldown from ts
          - new     `{addr: [ts, secs]}` — explicit (timestamp, cooldown_secs)
        """
        try:
            if os.path.exists(self._dip_loss_cooldown_path):
                with open(self._dip_loss_cooldown_path) as f:
                    raw = json.load(f)
                now = time.time()
                self._dip_loss_cooldown = {}
                for k, v in raw.items():
                    if isinstance(v, (list, tuple)) and len(v) == 2:
                        ts, secs = float(v[0]), float(v[1])
                    elif isinstance(v, (int, float)):
                        ts, secs = float(v), 1800.0  # legacy default
                    else:
                        continue
                    # Keep only entries whose deadline hasn't passed yet
                    if (now - ts) < secs:
                        self._dip_loss_cooldown[k] = [ts, secs]
                if self._dip_loss_cooldown:
                    logger.info(
                        f"[Trader] Loaded {len(self._dip_loss_cooldown)} "
                        f"dip-buy cooldowns from disk"
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

    async def _track_stop_recovery(
        self,
        token_address: str,
        token_symbol: str,
        entry_price: float,
        exit_price: float,
        reason: str,
    ) -> None:
        """
        Snapshot price at +30m / +1h / +4h after a dip_buy stop and append to
        /data/stop_recovery_log.jsonl. Tests whether the -10% stop is exiting
        tokens that recover (selection-bias check on the drawdown analysis
        that drove the stop-tightening: original analysis only counted winners
        we held, by construction excluding any that were stopped before they
        could recover).

        Append-only JSONL — restart-safe per-snapshot. If the bot restarts
        mid-window, snapshots after restart are lost (acceptable; this is
        instrumentation, not a behavior trigger). Best-effort price reads
        via the same _get_token_price fallback used at entry/exit.
        """
        if entry_price <= 0 or exit_price <= 0:
            return
        log_path = os.path.join(_DATA_DIR, "stop_recovery_log.jsonl")
        # Initial event: stop occurred
        try:
            with open(log_path, "a") as f:
                json.dump({
                    "type": "stop",
                    "token": token_symbol,
                    "mint": token_address,
                    "entry_price": float(entry_price),
                    "exit_price": float(exit_price),
                    "exit_pnl_pct": (exit_price / entry_price - 1) * 100 if entry_price > 0 else 0,
                    "reason": reason,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }, f)
                f.write("\n")
        except Exception as e:
            logger.warning(f"[stop_recovery] init write error: {e}")

        for delay_secs in (1800, 3600, 14400):  # 30m, 1h, 4h
            try:
                await asyncio.sleep(delay_secs)
                price = await self._get_token_price(token_address)
                if price is None or price <= 0:
                    continue
                vs_exit_pct = (price / exit_price - 1) * 100
                vs_entry_pct = (price / entry_price - 1) * 100
                with open(log_path, "a") as f:
                    json.dump({
                        "type": "snap",
                        "mint": token_address,
                        "delay_secs": delay_secs,
                        "price": float(price),
                        "vs_exit_pct": round(vs_exit_pct, 2),
                        "vs_entry_pct": round(vs_entry_pct, 2),
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }, f)
                    f.write("\n")
                logger.info(
                    f"[stop_recovery] {token_symbol} +{delay_secs//60}m: "
                    f"price={price:.6g} vs_exit={vs_exit_pct:+.1f}% vs_entry={vs_entry_pct:+.1f}%"
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"[stop_recovery] snap error for {token_symbol}: {e}")

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
        # Re-subscribe restored positions with their pair_address so the
        # feed pins each token to the exact pool we bought on. Without
        # this, multi-pair tokens fall back to highest-liquidity pair
        # selection which can produce 30x+ price discrepancies (PENGUIN
        # bug 2026-05-07: pumpswap entry $0.004 vs raydium tick $0.163).
        try:
            for _addr, _pos in self.open_positions.items():
                _pair = getattr(_pos, "pair_address", "") or ""
                if _pair:
                    feed.subscribe_token(
                        _addr,
                        chain_id=getattr(_pos, "chain_id", "solana") or "solana",
                        pair_address=_pair,
                    )
        except Exception as _e:
            logger.warning(f"[Trader] dex feed re-subscribe error: {_e}")

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
        if self._dashboard_paused:
            logger.info(f"[Trader] Buy blocked — dashboard pause active ({strategy}/{token_symbol})")
            return
        # Trading-hours window (Central Time). Historical analysis shows
        # 20-24 UTC (3-7pm CT) is the worst window: 66.7% WR / -$8/trade.
        # Default 6-15 CT (~11-20 UTC, depending on DST) brackets the
        # historical strong windows (8-12 UTC and 12-16 UTC, +$6-8/trade).
        # Only gates new buys; sells/TPs/stops continue normally.
        try:
            _start_h = int(os.environ.get("TRADING_START_HOUR_CT", "6"))
            _end_h = int(os.environ.get("TRADING_END_HOUR_CT", "15"))
            from zoneinfo import ZoneInfo as _ZI
            _now_ct = datetime.now(_ZI("America/Chicago"))
            _h = _now_ct.hour
            if not (_start_h <= _h < _end_h):
                logger.info(
                    f"[Trader] Buy blocked — outside trading window "
                    f"(now {_now_ct.strftime('%H:%M CT')}, window {_start_h:02d}-{_end_h:02d}) "
                    f"({strategy}/{token_symbol})"
                )
                return
        except Exception as _tz_err:
            logger.warning(f"[Trader] Trading-hours check failed (allowing buy): {_tz_err}")
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
            _rc: Optional[dict] = None
            _lp_locked_pct_at_entry: Optional[float] = None
            _dominant_pool_burned: bool = False
            if self._security_checker is not None and strategy != "graduation":
                try:
                    _rc = await self._security_checker._fetch_rugcheck(token_address)
                    if _rc and not _rc.get("_invalid_address"):
                        _lp_pct = _rc.get("lpLockedPct")
                        if _lp_pct is not None:
                            _lp_locked_pct_at_entry = float(_lp_pct or 0)
                        # Rugcheck `lpLockedPct` is DEX-inconsistent for burned LP:
                        # Meteora pools count burn as 100% locked; Orca pools count
                        # burn as 0% locked (treats absence of lock contract as unlocked).
                        # Burn (mintLP = system address) is functionally MORE secure
                        # than lock — tokens can never be redeemed. So when summary
                        # says lp=0, fetch the full report and check if dominant pool
                        # is actually burned. Only adds latency in the rare lp=0 case.
                        if _lp_pct is not None and float(_lp_pct or 0) == 0.0:
                            try:
                                _rc_check = await self._security_checker._fetch_rugcheck_full(token_address)
                                if _rc_check and isinstance(_rc_check, dict):
                                    _mkts = _rc_check.get("markets") or []
                                    if isinstance(_mkts, list) and _mkts:
                                        _best = None
                                        _best_liq = -1.0
                                        for _m in _mkts:
                                            if not isinstance(_m, dict):
                                                continue
                                            _lp_d = _m.get("lp") or {}
                                            _liq = float(_lp_d.get("quoteUSD", 0) or 0)
                                            if _liq > _best_liq:
                                                _best_liq = _liq
                                                _best = _m
                                        if _best is not None:
                                            _mint_lp = (_best.get("mintLP") or "")
                                            if _mint_lp == "11111111111111111111111111111111":
                                                _dominant_pool_burned = True
                                                _lp_locked_pct_at_entry = 100.0
                                                logger.info(
                                                    f"[Trader] LP BURN DETECTED: {token_symbol} "
                                                    f"({token_address[:8]}…) — dominant pool "
                                                    f"{_best.get('marketType','?')} mintLP=null "
                                                    f"(burned). Overriding lp_locked_pct → 100"
                                                )
                            except Exception:
                                pass  # fail-open — fall through to unlock-block
                            if not _dominant_pool_burned:
                                logger.warning(
                                    f"[Trader] LP UNLOCK BLOCK: {token_symbol} "
                                    f"({token_address[:8]}…) — LP unlocked since scan, skipping buy"
                                )
                                return
                except Exception:
                    pass  # fail-open — never block a buy due to rugcheck API failure

            # Capture holder-concentration + sol_price for entry_meta (analytics).
            # `_fetch_rugcheck` uses `/report/summary` which does NOT include
            # topHolders or creator_address; for analytics we fetch the FULL
            # `/report` endpoint here (~200ms extra, fail-soft).  This was a
            # known gap: top10_holder_pct was 0/132 in trade history before
            # this fix.
            _buy_time_meta: Dict[str, float] = {}
            if _lp_locked_pct_at_entry is not None:
                _buy_time_meta["lp_locked_pct"] = round(_lp_locked_pct_at_entry, 2)
            # Rugcheck risk score (0..100, higher = riskier). Already in the
            # summary response from the LP re-check above — capture for
            # forward correlation against trade outcomes.
            try:
                if _rc and isinstance(_rc, dict):
                    _score = _rc.get("score_normalised")
                    if _score is not None:
                        _buy_time_meta["rugcheck_score"] = round(float(_score), 2)
            except Exception:
                pass
            _rc_full: Optional[dict] = None
            if self._security_checker is not None and strategy != "graduation":
                try:
                    _rc_full = await self._security_checker._fetch_rugcheck_full(token_address)
                except Exception:
                    _rc_full = None
            try:
                if _rc_full and isinstance(_rc_full, dict):
                    _LP_TAGS = {"lp", "liquidity", "liquiditypool", "pool", "amm", "bonding curve"}
                    th = _rc_full.get("topHolders") or []
                    if isinstance(th, list) and th:
                        real = [
                            h for h in th
                            if isinstance(h, dict)
                            and h.get("insider", False) is not True
                            and (h.get("tag", "") or "").lower().strip() not in _LP_TAGS
                        ]
                        # topHolders uses `pct` as a percent value already (e.g. 12.5)
                        top10 = sum(float(h.get("pct", 0) or 0) for h in real[:10])
                        _buy_time_meta["top10_holder_pct"] = round(top10, 2)
                        # Single-largest-wallet concentration. One whale at 30%
                        # is qualitatively different from 10 wallets at 3% each
                        # (same top10 total, very different rug risk). Treated
                        # as its own signal for memecoin filter validation.
                        if real:
                            top1 = float(real[0].get("pct", 0) or 0)
                            _buy_time_meta["top1_holder_pct"] = round(top1, 2)
                            # Top-1 share of top-10. ≥0.5 = single-whale-dominant
                            # cluster (most danger). ≤0.2 = evenly distributed
                            # within the top 10 (relatively healthy).
                            if top10 > 0:
                                _buy_time_meta["top1_share_of_top10"] = round(top1 / top10, 3)
                    # Rugcheck creator field is `creator_address` (per honeypot.py:568).
                    creator = (_rc_full.get("creator_address") or "").lower()
                    if creator:
                        # Two list shapes:
                        #   `holders`     uses `percent` as a 0..1 FRACTION (×100 to %)
                        #   `topHolders`  uses `pct` as a 0..100 PERCENT directly
                        # Address may be in either `account` or `address`.
                        dev_pct = None
                        full_holders = _rc_full.get("holders") or []
                        if isinstance(full_holders, list):
                            for h in full_holders:
                                if not isinstance(h, dict):
                                    continue
                                addr = (h.get("account") or h.get("address") or "").lower()
                                if addr == creator:
                                    dev_pct = float(h.get("percent", 0) or 0) * 100
                                    break
                        if dev_pct is None and isinstance(th, list):
                            for h in th:
                                if not isinstance(h, dict):
                                    continue
                                addr = (h.get("address") or h.get("account") or "").lower()
                                if addr == creator:
                                    dev_pct = float(h.get("pct", 0) or 0)
                                    break
                        if dev_pct is not None:
                            _buy_time_meta["dev_holder_pct"] = round(dev_pct, 2)
            except Exception:
                pass

            # Single-sided LP detection — the dominant pool from the rugcheck
            # `markets` array is checked for side imbalance. baseUSD ≈ quoteUSD
            # is healthy (Raydium AMM seeded both sides). If one side is far
            # larger than the other, sells will drain the small side fast and
            # spike slippage. We pick the pool with the largest combined USD
            # depth (Jupiter typically routes through it) and flag if the
            # ratio max/min exceeds 5x. lp_imbalance_ratio is captured raw
            # for forward tuning of the threshold.
            try:
                if _rc_full and isinstance(_rc_full, dict):
                    _markets = _rc_full.get("markets") or []
                    if isinstance(_markets, list) and _markets:
                        _best_lp = None
                        _best_depth = -1.0
                        for _m in _markets:
                            if not isinstance(_m, dict):
                                continue
                            _lp = _m.get("lp") or {}
                            if not isinstance(_lp, dict):
                                continue
                            _b = float(_lp.get("baseUSD") or 0)
                            _q = float(_lp.get("quoteUSD") or 0)
                            _depth = _b + _q
                            if _depth > _best_depth:
                                _best_depth = _depth
                                _best_lp = (_b, _q)
                        if _best_lp and _best_depth > 0:
                            _b, _q = _best_lp
                            _hi = max(_b, _q)
                            _lo = max(min(_b, _q), 0.01)
                            _ratio = _hi / _lo
                            _buy_time_meta["lp_imbalance_ratio"] = round(_ratio, 3)
                            _buy_time_meta["lp_single_sided"] = bool(_ratio > 5.0)
                            _buy_time_meta["lp_dominant_depth_usd"] = round(_best_depth, 2)
            except Exception:
                pass

            # SOL/USD price snapshot at entry.  Use _get_token_price which has
            # Jupiter/DexScreener fallback — SOL is never subscribed to any
            # price-feed cache so cache-only lookup always returned None.
            try:
                _sol_price = await self._get_token_price(SOL_MINT)
                if _sol_price > 0:
                    _buy_time_meta["sol_price_usd_at_entry"] = round(float(_sol_price), 4)
            except Exception:
                pass

            # Wallet SOL balance at entry — gas-pressure context. Live-mode
            # only (paper mode has no wallet). Used for forensics if a swap
            # fails: low SOL right before a buy can starve priority fees.
            # Read from cache (30s TTL) — already fresh from live-mode buy
            # path's gas-reserve check; harmless extra read in paper mode
            # (returns -1, which we skip).
            try:
                if self.private_key:
                    _wsol = await self._get_sol_balance()
                    if _wsol >= 0:
                        _buy_time_meta["wallet_sol_balance_at_entry"] = round(float(_wsol), 4)
            except Exception:
                pass

            # Priority-fee snapshot for analytics: cap (lamports) and
            # priority level. The actual fee Jupiter pays is dynamic and
            # not directly returned in the swap response; the cap+level
            # are the bot-side configuration that bounds what we paid.
            try:
                _buy_time_meta["priority_fee_max_lamports"] = int(getattr(self, "_max_priority_lamports", 0) or 0)
                _pl = getattr(self, "_priority_level", "") or ""
                if _pl:
                    _buy_time_meta["priority_level"] = str(_pl)
            except Exception:
                pass

            # Merge buy-time meta into caller-provided entry_meta (caller wins
            # on conflicts so DipScanner-computed values aren't overwritten).
            if _buy_time_meta:
                entry_meta = {**_buy_time_meta, **(entry_meta or {})}

            # ── filter_quad SHADOW (logs only, never blocks) ─────────────────
            # 4-component combo identified by combinatorial optimization on the
            # 2026-05-03 baseline-mode dataset (n=223).  Best robust combo
            # across both halves of a chronological train/test split:
            #   TRAIN: 64 kept / 57.8% WR / +$10.38
            #   TEST:  40 kept / 57.5% WR / +$10.06
            #   FULL:  104 kept / 57.7% WR [48.0-66.9] / +$20.43 (vs -$72.79 baseline)
            # Blocks 53% of dip-buy entries — half the trade flow.
            #
            # Components (block when ANY match):
            #   F10 velocity_verdict == "QUIET"             — dead order flow
            #   F12 chart_stop_cluster_5m_pct_below ∈ [1.26%, 3.78%) — stop magnet
            #   F18 lp_locked_pct ∈ [60.15%, 78.90%)        — partial-lock band
            #   F19 1m_volume_spike ∈ [0.31, 0.80)          — fakeout zone
            # Each component picked from full-dataset single-feature outcome
            # stats (not auto-fitted on a sub-sample — overfit risk minimized).
            # Fail-open on missing inputs (None values).
            #
            # Shadow only: this code logs the would-be verdict and adds it to
            # entry_meta but never returns / skips. Promotion to enforced
            # requires another 200+ trades of held-out validation showing
            # WR ~ 58% on the kept set.
            if entry_meta is not None and isinstance(entry_meta, dict):
                _q_block_reasons: List[str] = []
                _q_v = entry_meta.get("velocity_verdict")
                if _q_v == "QUIET":
                    _q_block_reasons.append("velocity_verdict==QUIET")
                _q_sc = entry_meta.get("chart_stop_cluster_5m_pct_below")
                if _q_sc is not None:
                    try:
                        if 1.26 <= float(_q_sc) < 3.78:
                            _q_block_reasons.append(
                                f"stop_cluster_5m_pct_below={float(_q_sc):.2f}∈[1.26,3.78)"
                            )
                    except Exception:
                        pass
                _q_lp = entry_meta.get("lp_locked_pct")
                if _q_lp is not None:
                    try:
                        if 60.15 <= float(_q_lp) < 78.90:
                            _q_block_reasons.append(
                                f"lp_locked_pct={float(_q_lp):.2f}∈[60.15,78.90)"
                            )
                    except Exception:
                        pass
                _q_vs = entry_meta.get("1m_volume_spike")
                if _q_vs is not None:
                    try:
                        if 0.31 <= float(_q_vs) < 0.80:
                            _q_block_reasons.append(
                                f"1m_volume_spike={float(_q_vs):.3f}∈[0.31,0.80)"
                            )
                    except Exception:
                        pass
                _q_verdict = "BLOCK" if _q_block_reasons else "PASS"
                entry_meta["filter_quad_verdict"] = _q_verdict
                entry_meta["filter_quad_block_reasons"] = _q_block_reasons
                if _q_verdict == "BLOCK":
                    logger.info(
                        f"[Trader] filter_quad SHADOW would-block: {token_symbol} "
                        f"reasons={','.join(_q_block_reasons)}"
                    )

            # ── filter_quad_robust SHADOW (logs only, never blocks) ──────────
            # 6-component OR-block combo, train/test-validated on the
            # 2026-05-03 baseline-mode dataset (n=223). Stronger signal than
            # filter_quad: keeps fewer trades but at higher WR.
            #   TRAIN: 32 kept / 65.6% WR / +$9.09
            #   TEST:  16 kept / 68.8% WR / +$10.34
            #   FULL:  48 kept / 66.7% WR [52.5-78.3] / +$19.43 vs -$72.79 baseline
            # Blocks 78% of dip-buy entries — much tighter than quad (53%).
            #
            # Components (block when ANY match):
            #   chart_structure_15m_verdict == "TREND_UP"   — already-up 15m
            #   peak_h24_6h_pct > 356                       — already mooned
            #   peak_h24_6h_pct < -17                       — capitulation tape
            #   velocity_verdict == "QUIET"                 — dead order flow
            #   top10_holder_pct > 60.15                    — concentration
            #   lp_locked_pct ∈ [60.15%, 78.90%)            — partial-lock band
            if entry_meta is not None and isinstance(entry_meta, dict):
                _r_block_reasons: List[str] = []
                if entry_meta.get("chart_structure_15m_verdict") == "TREND_UP":
                    _r_block_reasons.append("struct15m==TREND_UP")
                if entry_meta.get("velocity_verdict") == "QUIET":
                    _r_block_reasons.append("velocity_verdict==QUIET")
                _r_pk = entry_meta.get("peak_h24_6h_pct")
                if _r_pk is not None:
                    try:
                        _pkv = float(_r_pk)
                        if _pkv > 356:
                            _r_block_reasons.append(f"peak_h24_6h_pct={_pkv:.1f}>356")
                        elif _pkv < -17:
                            _r_block_reasons.append(f"peak_h24_6h_pct={_pkv:.1f}<-17")
                    except Exception:
                        pass
                _r_t10 = entry_meta.get("top10_holder_pct")
                if _r_t10 is not None:
                    try:
                        if float(_r_t10) > 60.15:
                            _r_block_reasons.append(f"top10_holder_pct={float(_r_t10):.2f}>60.15")
                    except Exception:
                        pass
                _r_lp = entry_meta.get("lp_locked_pct")
                if _r_lp is not None:
                    try:
                        if 60.15 <= float(_r_lp) < 78.90:
                            _r_block_reasons.append(
                                f"lp_locked_pct={float(_r_lp):.2f}∈[60.15,78.90)"
                            )
                    except Exception:
                        pass
                _r_verdict = "BLOCK" if _r_block_reasons else "PASS"
                entry_meta["filter_quad_robust_verdict"] = _r_verdict
                entry_meta["filter_quad_robust_block_reasons"] = _r_block_reasons
                if _r_verdict == "BLOCK":
                    logger.info(
                        f"[Trader] filter_quad_robust SHADOW would-block: {token_symbol} "
                        f"reasons={','.join(_r_block_reasons)}"
                    )

            # ── filter_quad_hi_wr SHADOW (logs only, never blocks) ───────────
            # Hybrid combo (OR-block + AND-allow). Strictest filter — keeps
            # only ~4% of dip-buys but at 100% WR on the 2026-05-03 dataset.
            #   TRAIN: 6 kept / 100% WR / +$10.70
            #   TEST:  3 kept / 100% WR / +$8.18
            #   FULL:  9 kept / 100% WR [70.1-100] / +$18.89 vs -$72.79 baseline
            # n=9 is small — Wilson lower bound 70.1%, point estimate could
            # land anywhere in [70%, 100%] on fresh data. Need ~30+ hits to
            # tighten the CI before considering enforcement.
            #
            # Effective rule (BLOCK if ANY of the following):
            #   chart_structure_15m_verdict == "TREND_UP"
            #   top10_holder_pct > 60.15
            #   chart_mtf_alignment == "strong_bull"
            #   concurrent_positions_at_entry ∈ [11, 14]   — bot-state, weird
            #     but in-sample-significant; treat with skepticism
            #   lp_locked_pct ≤ 78.90  (allow only top quartile)
            #   1m_volume_spike ≤ 0.80 (allow only top quartile)
            if entry_meta is not None and isinstance(entry_meta, dict):
                _h_block_reasons: List[str] = []
                if entry_meta.get("chart_structure_15m_verdict") == "TREND_UP":
                    _h_block_reasons.append("struct15m==TREND_UP")
                if entry_meta.get("chart_mtf_alignment") == "strong_bull":
                    _h_block_reasons.append("mtf==strong_bull")
                _h_t10 = entry_meta.get("top10_holder_pct")
                if _h_t10 is not None:
                    try:
                        if float(_h_t10) > 60.15:
                            _h_block_reasons.append(f"top10_holder_pct={float(_h_t10):.2f}>60.15")
                    except Exception:
                        pass
                _h_cp = entry_meta.get("concurrent_positions_at_entry")
                if _h_cp is not None:
                    try:
                        if 11 <= float(_h_cp) < 14:
                            _h_block_reasons.append(f"concurrent_positions={int(float(_h_cp))}∈[11,14)")
                    except Exception:
                        pass
                _h_lp = entry_meta.get("lp_locked_pct")
                if _h_lp is not None:
                    try:
                        if float(_h_lp) <= 78.90:
                            _h_block_reasons.append(f"lp_locked_pct={float(_h_lp):.2f}<=78.90")
                    except Exception:
                        pass
                else:
                    _h_block_reasons.append("lp_locked_pct=missing")
                _h_vs = entry_meta.get("1m_volume_spike")
                if _h_vs is not None:
                    try:
                        if float(_h_vs) <= 0.80:
                            _h_block_reasons.append(f"1m_volume_spike={float(_h_vs):.3f}<=0.80")
                    except Exception:
                        pass
                else:
                    _h_block_reasons.append("1m_volume_spike=missing")
                _h_verdict = "BLOCK" if _h_block_reasons else "PASS"
                entry_meta["filter_quad_hi_wr_verdict"] = _h_verdict
                entry_meta["filter_quad_hi_wr_block_reasons"] = _h_block_reasons
                if _h_verdict == "PASS":
                    logger.info(
                        f"[Trader] filter_quad_hi_wr SHADOW would-ALLOW: {token_symbol} "
                        f"(rare 100%-WR cohort match)"
                    )

            # ── filter_combo_v2 — ENFORCED 2026-05-05 ───────────────────────
            # Pareto-best 50%-block combo from scripts/filter_combo_pareto.py
            # on post-Apr-30 cohort (n=466, 4.6 days):
            #
            # Block if ANY of these 5 match (OR-block):
            #   - lp_locked_pct ∈ [60.15%, 78.90%)  — partial-lock band
            #   - chart_structure_5m_verdict == "REVERSAL_UP"  — bullish-reversal trap
            #   - peak_h24_6h_pct > 500             — already mooned
            #   - chart_mtf_alignment == "strong_bull"  — post-pump-corpse trap
            #   - chart_trendline_5m_verdict == "BLOCK"  — at structural ceiling
            #
            # Performance on the post-Apr-30 cohort:
            #   kept=188 (40% of 466)  block_pct=60%
            #   WR=64.9%  CI_lo=57.8%
            #   total_pnl=+$59.28  per_trade=+$0.315
            #   est daily PnL: +$12.78/day (vs unfiltered baseline -$14.63/day)
            #
            # Why dip_scanner doesn't enforce this: lp_locked_pct is fetched
            # post-rugcheck in this method (above), so the full combo can
            # only be evaluated here. Other 4 features ARE in entry_meta from
            # dip_scanner — the cost of moving to trader-level is one extra
            # rugcheck call per blocked candidate (acceptable).
            #
            # Each component fail-opens (None values don't trigger).
            if entry_meta is not None and isinstance(entry_meta, dict):
                _v2_block_reasons: List[str] = []
                _v2_lp = entry_meta.get("lp_locked_pct")
                if _v2_lp is not None:
                    try:
                        if 60.15 <= float(_v2_lp) < 78.90:
                            _v2_block_reasons.append(
                                f"lp_locked_pct={float(_v2_lp):.2f}∈[60.15,78.90)"
                            )
                    except Exception:
                        pass
                if entry_meta.get("chart_structure_5m_verdict") == "REVERSAL_UP":
                    _v2_block_reasons.append("chart_structure_5m==REVERSAL_UP")
                # peak>500 was effectively blocking 97.5% of young (<24h) tokens —
                # the band is dominated by "post-launch pump" pattern, not "already
                # mooned" signal. Among post-Apr-30:
                #   peak>500 + age<24h: n=78, avg -$0.28 (≈baseline -$0.30)
                #   peak>500 + age>=24h: n=45, avg -$0.74 (clearly bad)
                # Restricting to age>=24h preserves the signal on truly-mooned tokens
                # without the age-filter side effect.
                _v2_pk = entry_meta.get("peak_h24_6h_pct")
                if _v2_pk is not None:
                    try:
                        if float(_v2_pk) > 500 and age_hours >= 24:
                            _v2_block_reasons.append(
                                f"peak_h24={float(_v2_pk):.0f}%>500% AND age={age_hours:.1f}h>=24h"
                            )
                    except Exception:
                        pass
                if entry_meta.get("chart_mtf_alignment") == "strong_bull":
                    _v2_block_reasons.append("chart_mtf_alignment==strong_bull")
                if entry_meta.get("chart_trendline_5m_verdict") == "BLOCK":
                    _v2_block_reasons.append("chart_trendline_5m==BLOCK")
                _v2_verdict = "BLOCK" if _v2_block_reasons else "PASS"
                entry_meta["filter_combo_v2_verdict"] = _v2_verdict
                entry_meta["filter_combo_v2_block_reasons"] = _v2_block_reasons
                # Apply ONLY to dip_buy strategy.  Other strategies (scalp,
                # graduation, MC) have their own setups and signal pipelines.
                if _v2_verdict == "BLOCK" and strategy == "dip_buy":
                    logger.info(
                        f"[Trader] BLOCKED by filter_combo_v2: {token_symbol} "
                        f"reasons={','.join(_v2_block_reasons)}"
                    )
                    return

            # ── filter_chart_bear — ENFORCED 2026-05-05 ──────────────────────
            # Counterpart to filter_combo_v2. v2 catches over-pumped traps
            # (strong_bull / REVERSAL_UP / peak>500%); this catches actively-
            # bleeding charts the bot was buying anyway because nothing else
            # said no.
            #
            # Trigger cases:
            #   EITHER 14:39 2026-05-05 — descending_triangle 95.1% +
            #                              REVERSAL_DOWN + trendline_breakdown
            #   maxxing 14:50 2026-05-05 — strong_bear (1m/5m/15m all bear) +
            #                              strong_bearish marubozu + REVERSAL_DOWN
            #
            # Block if ANY of these 3 match:
            #   1. chart_mtf_alignment == "strong_bear"
            #        (lifetime: n=102, WR=24.5%, total -$41.97)
            #   2. chart_candle_confluence == "strong_bearish"
            #        (lifetime: n=94,  WR=25.5%, total -$46.50)
            #   3. chart_pattern_5m_dir == "bearish" AND conf >= 80 AND
            #      chart_structure_5m_verdict == "REVERSAL_DOWN"
            #        (catches EITHER's descending_triangle@95% pattern)
            #
            # Lifetime validation (full_coverage=True only, n=449):
            #   BLOCK n=177 WR=24.3% total -$79.34 (-$0.45/trade)
            #   ALLOW n=272 WR=36.0% total  -$3.40 (-$0.01/trade — breakeven)
            #   block_rate=39.4% — high but blocks are clearly losers
            #
            # Fail-open: any None field passes its individual check.
            if entry_meta is not None and isinstance(entry_meta, dict):
                _b_block_reasons: List[str] = []
                if entry_meta.get("chart_mtf_alignment") == "strong_bear":
                    _b_block_reasons.append("chart_mtf_alignment==strong_bear")
                if entry_meta.get("chart_candle_confluence") == "strong_bearish":
                    _b_block_reasons.append("chart_candle_confluence==strong_bearish")
                _b_pdir = entry_meta.get("chart_pattern_5m_dir")
                _b_pconf = entry_meta.get("chart_pattern_5m_conf")
                _b_struct = entry_meta.get("chart_structure_5m_verdict")
                try:
                    if (
                        _b_pdir == "bearish"
                        and _b_pconf is not None
                        and float(_b_pconf) >= 80
                        and _b_struct == "REVERSAL_DOWN"
                    ):
                        _b_block_reasons.append(
                            f"pattern_5m={entry_meta.get('chart_pattern_5m')}@"
                            f"{float(_b_pconf):.1f}%(bearish)+struct_5m=REVERSAL_DOWN"
                        )
                except Exception:
                    pass
                _b_verdict = "BLOCK" if _b_block_reasons else "PASS"
                entry_meta["filter_chart_bear_verdict"] = _b_verdict
                entry_meta["filter_chart_bear_block_reasons"] = _b_block_reasons
                if _b_verdict == "BLOCK" and strategy == "dip_buy":
                    logger.info(
                        f"[Trader] BLOCKED by filter_chart_bear: {token_symbol} "
                        f"reasons={','.join(_b_block_reasons)}"
                    )
                    return

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
                    # Pass pair_address so the DexScreener feed pins to the
                    # exact pool we bought on — multi-pair tokens can have
                    # 30x+ priceUsd discrepancies between DEXes.
                    self._dex_price_feed.subscribe_token(
                        token_address,
                        chain_id=chain_id,
                        pair_address=pair_address,
                    )
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

                # Slot delay / signal-to-fill latency (paper-mode equivalent —
                # measures bot-internal time between signal-fire and Position
                # construction, not on-chain confirmation).
                try:
                    _sig_ms = (entry_meta or {}).get("signal_ts_ms")
                    if _sig_ms:
                        _delay_ms = int(time.time() * 1000) - int(_sig_ms)
                        entry_meta = {**(entry_meta or {}), "signal_to_fill_ms": _delay_ms}
                except Exception:
                    pass

                # Preserve original-case mint on the Position — Solana base58
                # is case-sensitive; lowercased mints are rejected by Jupiter
                # and Solana RPC ("WrongSize" error).  Dict key stays lowercased
                # for case-insensitive lookups.  See incident 2026-04-29.
                position = Position(
                    token_address=token_address,
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
            # Pre-trade gate: ensure wallet has enough SOL for gas reserve.
            # Blocks new entries when wallet would drop below MIN_SOL_RESERVE
            # after this trade.  Sells are NOT gated (we always want to be able
            # to exit a position even if gas is tight).
            if not await self._check_sol_reserve(token_symbol):
                return
            # Get SOL amount for position size
            sol_amount = await self._usd_to_sol(position_size_usd)
            if sol_amount <= 0:
                return

            # Fetch mint decimals before quoting — used to convert Jupiter's
            # raw atomic outAmount to human-readable token units.  pump.fun = 6,
            # most others = 9.  Hardcoding 1e9 produces 1000× position sizing
            # errors on 6-decimal tokens (caught live with TripleT 2026-04-28).
            token_decimals = await self._get_token_decimals(token_address)

            # Quote + swap with retry — same pattern as live sell.  Buys are
            # less time-sensitive than stops, but transient network/RPC issues
            # still warrant a retry rather than silently aborting the entry.
            quote: Optional[dict] = None
            out_amount = 0
            entry_price = 0.0
            success = False
            for _attempt in range(3):
                quote = await self._get_quote(
                    input_mint=SOL_MINT,
                    output_mint=token_address,
                    amount=int(sol_amount * 1e9),
                )
                if not quote:
                    if _attempt < 2:
                        await asyncio.sleep(2 ** _attempt)
                        continue
                    logger.error(f"No quote available for {token_symbol} after 3 attempts")
                    return
                out_amount = int(quote.get("outAmount", 0))
                amount_tokens = out_amount / (10 ** token_decimals) if out_amount > 0 else 0
                entry_price = position_size_usd / amount_tokens if amount_tokens > 0 else 0
                success = await self._execute_swap(quote)
                if success:
                    break
                if _attempt < 2:
                    logger.warning(
                        f"[Trader] Live buy {token_symbol}: swap failed "
                        f"(attempt {_attempt+1}/3), retrying..."
                    )
                    await asyncio.sleep(2 ** _attempt)
            if not success:
                logger.error(f"Swap failed for {token_symbol} after 3 attempts")
                return

            # Slot delay / signal-to-fill latency. signal_ts_ms is set by
            # DipScanner at signal-fire time; we now have on-chain confirmation
            # so we can compute the gap. High latency = late entries (buying
            # the bounce, not the dip) — flagged in claude-ideas as a Solana-
            # specific microstructure killer.
            try:
                _sig_ms = (entry_meta or {}).get("signal_ts_ms")
                if _sig_ms:
                    _delay_ms = int(time.time() * 1000) - int(_sig_ms)
                    entry_meta = {**(entry_meta or {}), "signal_to_fill_ms": _delay_ms}
            except Exception:
                pass

            # Preserve original-case mint on Position.token_address — required
            # for Jupiter/RPC sells (case-sensitive base58).  See 2026-04-29.
            position = Position(
                token_address=token_address,
                token_symbol=token_symbol,
                entry_price_usd=entry_price,
                amount_tokens=amount_tokens,
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
                token_decimals=token_decimals,
            )
            self.open_positions[token_address.lower()] = position
            self._save_open_positions()
            self.reentry.buy_counts[token_address.lower()] = self.reentry.buy_counts.get(token_address.lower(), 0) + 1
            if strategy != "scalp":
                self.risk_manager.record_buy(position_size_usd)

            # Subscribe real-time price feeds for live position
            if self._axiom_price_feed is not None:
                self._axiom_price_feed.subscribe_token(token_address)
            if self._dex_price_feed is not None:
                self._dex_price_feed.subscribe_token(
                    token_address,
                    chain_id=chain_id,
                    pair_address=pair_address,
                )
            if self._rpc_price_feed is not None:
                _proto = "pump amm" if "pump amm" in reason.lower() else ""
                self._rpc_price_feed.subscribe_token(token_address, pool_type=_proto)

            _buy_realized = round(float(self._last_realized_slippage_pct or 0.0), 4)
            await self.telegram.send(
                f"✅ *Bought ${token_symbol}*\n\n"
                f"💵 Size: ${position_size_usd:.0f}\n"
                f"📉 Realized slip: {_buy_realized:+.3f}%\n"
                f"📝 Reason: {reason}"
            )
            self.tracker.record_buy(position)
            logger.info(f"✅ Bought {token_symbol} — ${position_size_usd:.0f} | realized_slip={_buy_realized:+.3f}%")

        except Exception as e:
            logger.error(f"Buy failed for {token_symbol}: {e}")
        finally:
            self._buying.discard(token_address.lower())

    async def sell(self, token_address: str, token_symbol: str, reason: str, pct: float = 1.0):
        """Execute a sell order for a percentage of the position.

        Returns a dict {ok: bool, reason: str, pnl_usd: float|None} so callers
        (notably the dashboard /api/sell handler) can distinguish silent
        retry-exhaustion failures from real successes. Existing call sites
        that ignore the return are unaffected.
        """
        # Use lowercased address ONLY for in-memory dict/set lookups.  External
        # API calls (Jupiter, RPC, price feeds) MUST receive the original-case
        # mint via position.token_address — Solana base58 is case-sensitive,
        # and Jupiter rejects lowercased mints with HTTP 400 "WrongSize".
        # Incident 2026-04-29: 19 hours of failed sells, 4 stranded positions.
        addr_key = token_address.lower()
        position = self.open_positions.get(addr_key)
        if not position:
            logger.warning(f"No position found for {token_symbol}")
            return {"ok": False, "reason": "position_not_found", "pnl_usd": None}

        # Prevent concurrent sells on the same token (race between CopyTrader and PositionManager)
        if addr_key in self._selling:
            logger.debug(f"[Trader] Sell already in progress for {token_symbol} — skipping duplicate")
            return {"ok": False, "reason": "already_selling", "pnl_usd": None}
        self._selling.add(addr_key)
        # Use the canonical mint from the Position for any external call below.
        token_address = position.token_address

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
                    del self.open_positions[addr_key]
                    self.reentry.previously_held.add(addr_key)
                    self.reentry.save()
                    # Unsubscribe from real-time feeds when position fully closed
                    if self._axiom_price_feed is not None:
                        self._axiom_price_feed.unsubscribe_token(token_address)
                    if self._dex_price_feed is not None:
                        self._dex_price_feed.unsubscribe_token(token_address)
                    if self._rpc_price_feed is not None:
                        self._rpc_price_feed.unsubscribe_token(token_address)
                    # Cooldown after ANY full dip_buy close — wins included.
                    # A successful TP2 exit signals "we just hit the top of
                    # this move" — re-entering 23s later (LASTMAN 22:59)
                    # buys the literal high.  Both losses and wins register
                    # the cooldown; the rebuy-after-win pattern is at least
                    # as bad as rebuy-after-loss because the win itself
                    # pumped the local price.  Volume-death closes get a
                    # longer 6h cooldown via _register_dip_close.
                    if getattr(position, "strategy", "") == "dip_buy":
                        self._register_dip_close(token_address, reason)
                        if "stop" in (reason or "").lower():
                            asyncio.create_task(self._track_stop_recovery(
                                token_address=token_address,
                                token_symbol=token_symbol,
                                entry_price=float(getattr(position, "entry_price_usd", 0) or 0),
                                exit_price=float(getattr(position, "current_price_usd", 0) or 0),
                                reason=reason,
                            ))
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
                _sell_meta = await self._snapshot_sell_time_meta(position)
                _em_with_snaps = {**(getattr(position, "entry_meta", None) or {}), "hold_pnl_snapshots": getattr(position, "hold_pnl_snapshots", None) or {}, **_sell_meta}
                self.tracker.record_sell(token_address, usd_received, pnl, reason, pnl_pct=round(pnl_pct, 2), max_drawdown_pct=max_drawdown_pct, hold_secs=_hold_secs, entry_market_cap_usd=getattr(position, "entry_market_cap_usd", 0.0), entry_age_hours=getattr(position, "entry_age_hours", 0.0), entry_volume_h1_usd=getattr(position, "entry_volume_h1_usd", 0.0), pair_address=getattr(position, "pair_address", "") or "", entry_meta=_em_with_snaps, peak_pnl_pct=getattr(position, "peak_pnl_pct", 0.0) or 0.0, peak_pnl_at_secs=getattr(position, "peak_pnl_at_secs", 0) or 0, exit_bs_h1=getattr(position, "current_bs_h1", 0.0) or 0.0, exit_bs_m5=getattr(position, "current_bs_m5", 0.0) or 0.0, realized_slippage_pct=round(float(impact_pct or 0), 4))
                logger.info(
                    f"{emoji} [PAPER] Sold {pct*100:.0f}% of {token_symbol} — "
                    f"PnL: ${pnl:+.2f} | Impact: {impact_pct:.2f}% | Source: {price_source}"
                )
                return {"ok": True, "reason": "sold_paper", "pnl_usd": round(pnl, 2)}

            # ── LIVE TRADING MODE ─────────────────────────────────────
            # Use the mint's actual decimals (recorded at buy time).  Falls back
            # to 6 (pump.fun convention) for legacy positions that pre-date the
            # token_decimals field — better to under-sell on first sweep than
            # to over-sell and fail the quote.
            _decimals = getattr(position, "token_decimals", 6) or 6
            tokens_to_sell = int(position.amount_tokens * pct * (10 ** _decimals))

            # Clamp tokens_to_sell to the on-chain ATA balance.  Stored
            # `position.amount_tokens` can drift from wallet truth due to buy-side
            # fees, prior partial sells, or rounding — when it exceeds wallet
            # balance Jupiter aborts the route at the cleanup-account step
            # (custom error 0x1788 / 6024 BalanceShouldBeReducedToZero).  At a
            # 100% sell we round to 99.9% of on-chain balance to leave dust headroom;
            # for partial sells we cap at on-chain balance so we never request more
            # than we own.
            try:
                onchain_atomic = await self._get_token_balance_atomic(token_address)
            except Exception:
                onchain_atomic = -1
            if onchain_atomic > 0:
                _cap = int(onchain_atomic * 0.999) if pct >= 0.999 else onchain_atomic
                if tokens_to_sell > _cap:
                    logger.info(
                        f"[Trader] Live sell {token_symbol}: clamping "
                        f"{tokens_to_sell} → {_cap} atomic units "
                        f"(stored amount_tokens exceeds on-chain ATA balance)"
                    )
                    tokens_to_sell = _cap
            elif onchain_atomic == 0:
                logger.error(
                    f"[Trader] Live sell {token_symbol}: on-chain ATA balance is 0 "
                    f"— position is a ghost; closing locally without swap"
                )
                return {"ok": False, "reason": "ghost_position_zero_balance", "pnl_usd": None}
            # onchain_atomic == -1 → RPC failure; fall through and let Jupiter try

            # Wider slippage tolerance for urgent exits (stop-loss + manual
            # sell from dashboard). Both are "exit at any price" priority; in
            # a fast crash or on a thin pair the token may move 1-3% between
            # quote and swap submission, causing 1%-tolerance swaps to reject.
            _r = reason.lower()
            _is_urgent_exit = ("stop" in _r) or ("manual" in _r)
            _slip_bps = 300 if _is_urgent_exit else 100

            # Retry: refetch quote + retry swap up to 3 times.  Fresh quote
            # each attempt because slippage failures often mean price moved past
            # the original quote's tolerance — same quote will keep failing.
            quote: Optional[dict] = None
            sol_received = 0.0
            usd_received = 0.0
            cost_basis = 0.0
            pnl = 0.0
            pnl_pct = 0.0
            success = False
            for _attempt in range(3):
                quote = await self._get_quote(
                    input_mint=token_address,
                    output_mint=SOL_MINT,
                    amount=tokens_to_sell,
                    slippage_bps=_slip_bps,
                )
                if not quote:
                    if _attempt < 2:
                        await asyncio.sleep(2 ** _attempt)
                        continue
                    logger.error(
                        f"[Trader] Live sell {token_symbol}: quote failed after 3 attempts — "
                        f"position remains open, will retry on next price tick"
                    )
                    return {"ok": False, "reason": "quote_failed_3x", "pnl_usd": None}
                sol_received = int(quote.get("outAmount", 0)) / 1e9
                usd_received = await self._sol_to_usd(sol_received)
                cost_basis = position.amount_usd * pct
                pnl = usd_received - cost_basis
                pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
                success = await self._execute_swap(quote)
                if success:
                    break
                if _attempt < 2:
                    logger.warning(
                        f"[Trader] Live sell {token_symbol}: swap failed "
                        f"(attempt {_attempt+1}/3, slip={_slip_bps}bps), retrying..."
                    )
                    await asyncio.sleep(2 ** _attempt)
            if not success:
                logger.error(
                    f"[Trader] Live sell {token_symbol}: swap failed 3x — "
                    f"position remains open, will retry on next price tick"
                )
                return {"ok": False, "reason": "swap_failed_3x", "pnl_usd": None}

            _min_p = getattr(position, "min_price_usd", 0)
            _entry = getattr(position, "entry_price_usd", 0)
            max_drawdown_pct = round((_min_p / _entry - 1) * 100, 2) if _entry > 0 and _min_p > 0 else 0.0

            if pct >= 1.0:
                del self.open_positions[addr_key]
                self.reentry.previously_held.add(addr_key)
                self.reentry.save()
                if self._axiom_price_feed is not None:
                    self._axiom_price_feed.unsubscribe_token(token_address)
                if self._dex_price_feed is not None:
                    self._dex_price_feed.unsubscribe_token(token_address)
                if self._rpc_price_feed is not None:
                    self._rpc_price_feed.unsubscribe_token(token_address)
                # Cooldown for dip_buy strategy on every full close.
                # Volume-death closes get extended 6h cooldown.
                if getattr(position, "strategy", "") == "dip_buy":
                    self._register_dip_close(token_address, reason)
                    if "stop" in (reason or "").lower():
                        asyncio.create_task(self._track_stop_recovery(
                            token_address=token_address,
                            token_symbol=token_symbol,
                            entry_price=float(getattr(position, "entry_price_usd", 0) or 0),
                            exit_price=float(getattr(position, "current_price_usd", 0) or 0),
                            reason=reason,
                        ))
            else:
                position.amount_tokens *= (1 - pct)
                position.amount_sol_spent *= (1 - pct)
                position.amount_usd *= (1 - pct)
            self._save_open_positions()

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
            # Realized slippage from on-chain balance delta, captured by
            # _execute_swap.  SOL-output sells are noisy (gas confounds the delta);
            # values here include gas as a small additive ~0.1%.
            _realized = round(float(self._last_realized_slippage_pct or 0.0), 4)
            _sell_meta = await self._snapshot_sell_time_meta(position)
            _em_with_snaps = {**(getattr(position, "entry_meta", None) or {}), "hold_pnl_snapshots": getattr(position, "hold_pnl_snapshots", None) or {}, **_sell_meta}
            self.tracker.record_sell(token_address, usd_received, pnl, reason, pnl_pct=round(pnl_pct, 2), max_drawdown_pct=max_drawdown_pct, hold_secs=_hold_secs, entry_market_cap_usd=getattr(position, "entry_market_cap_usd", 0.0), entry_age_hours=getattr(position, "entry_age_hours", 0.0), entry_volume_h1_usd=getattr(position, "entry_volume_h1_usd", 0.0), pair_address=getattr(position, "pair_address", "") or "", entry_meta=_em_with_snaps, peak_pnl_pct=getattr(position, "peak_pnl_pct", 0.0) or 0.0, peak_pnl_at_secs=getattr(position, "peak_pnl_at_secs", 0) or 0, exit_bs_h1=getattr(position, "current_bs_h1", 0.0) or 0.0, exit_bs_m5=getattr(position, "current_bs_m5", 0.0) or 0.0, realized_slippage_pct=_realized)
            logger.info(f"{emoji} Sold {pct*100:.0f}% of {token_symbol} — PnL: ${pnl:+.0f} | realized_slip={_realized:+.3f}%")
            return {"ok": True, "reason": "sold", "pnl_usd": round(pnl, 2)}

        except Exception as e:
            logger.error(f"Sell failed for {token_symbol}: {e}")
            return {"ok": False, "reason": f"exception: {type(e).__name__}: {e}", "pnl_usd": None}
        finally:
            self._selling.discard(addr_key)

    async def _get_quote(self, input_mint: str, output_mint: str, amount: int,
                         slippage_bps: int = 100) -> Optional[dict]:
        """Get a swap quote from Jupiter, with retries for transient DNS/network errors.
        slippage_bps: 100 (1%) default for normal trades; 300 (3%) for stop-loss exits
        where price may drift between quote and swap during fast crashes."""
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": slippage_bps,
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
        # Only counts when all 3 attempts failed.  Single-attempt failures within
        # a successful retry don't count — we want to track exhausted-retry events.
        if self.private_key:
            self._exec_stats["quote_failures"] += 1
        return None

    async def _execute_swap(self, quote: dict) -> bool:
        """
        Execute a swap using Jupiter.  In live mode, also captures realized
        slippage by comparing quote.outAmount to the actual on-chain balance
        delta of the output token.  Stores the result on
        self._last_realized_slippage_pct for the caller to read.
        """
        if not self.private_key:
            logger.warning("No private key set — skipping actual swap (paper trading mode)")
            return True  # Paper trading mode

        # Reset realized-slip state for this attempt.  Buy/sell paths read
        # self._last_realized_slippage_pct after a successful swap returns.
        self._last_realized_slippage_pct = 0.0
        self._exec_stats["swaps_attempted"] += 1

        # Pre-swap balance of the output token — needed to compute realized
        # slippage from the post-swap delta.  SOL (output side of sells) needs
        # special handling because gas fees confound the delta; we use a wider
        # tolerance for SOL and treat token-side as the authoritative measure.
        output_mint = quote.get("outputMint", "")
        expected_out = int(quote.get("outAmount", 0) or 0)
        pre_balance = -1
        is_sol_out = output_mint == SOL_MINT
        try:
            if is_sol_out:
                _sol_pre = await self._get_sol_balance(force=True)
                pre_balance = int(_sol_pre * 1e9) if _sol_pre >= 0 else -1
            elif output_mint:
                pre_balance = await self._get_token_balance_atomic(output_mint)
        except Exception as e:
            logger.debug(f"[Trader] pre-swap balance query failed: {e}")
            pre_balance = -1

        try:
            async with aiohttp.ClientSession(headers=_JUPITER_HEADERS) as session:
                # Adaptive priority fee — Jupiter computes from current congestion,
                # capped at MAX_PRIORITY_LAMPORTS env (default 1M lamports / 0.001 SOL).
                # Replaces the previous hardcoded 10000 lamports which was insufficient
                # during any meaningful network congestion.
                payload = {
                    "quoteResponse": quote,
                    "userPublicKey": self._get_public_key(),
                    "wrapAndUnwrapSol": True,
                    "prioritizationFeeLamports": {
                        "priorityLevelWithMaxLamports": {
                            "maxLamports": self._max_priority_lamports,
                            "priorityLevel": self._priority_level,
                        }
                    },
                }
                async with session.post(JUPITER_SWAP_API, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        self._exec_stats["swap_failures"] += 1
                        return False
                    swap_data = await resp.json()
                    swap_tx = swap_data.get("swapTransaction", "")
                    success = await self._send_transaction(swap_tx)
                    if not success:
                        self._exec_stats["swap_failures"] += 1
                        return False
                    self._exec_stats["successful_swaps"] += 1
        except Exception as e:
            logger.error(f"Swap execution error: {e}")
            self._exec_stats["swap_failures"] += 1
            return False

        # Realized slippage from balance delta.  Only attempted on success and
        # when pre-balance query worked.  Errors here are non-fatal — the swap
        # itself succeeded; we just won't have a slippage sample this round.
        if pre_balance >= 0 and expected_out > 0 and output_mint:
            try:
                # Wait briefly for post-confirmation balance to propagate to RPC.
                await asyncio.sleep(0.5)
                if is_sol_out:
                    _sol_post = await self._get_sol_balance(force=True)
                    post_balance = int(_sol_post * 1e9) if _sol_post >= 0 else -1
                else:
                    post_balance = await self._get_token_balance_atomic(output_mint)
                if post_balance >= 0 and post_balance > pre_balance:
                    actual_received = post_balance - pre_balance
                    # Slippage = (expected - actual) / expected × 100.  Positive
                    # means we got less than quoted (normal); negative means we
                    # got more (rare, but possible from mid-quote price improvement).
                    realized_pct = (expected_out - actual_received) / expected_out * 100
                    self._last_realized_slippage_pct = realized_pct
                    self._realized_slippage_history.append(realized_pct)
                    if len(self._realized_slippage_history) > 200:
                        self._realized_slippage_history = self._realized_slippage_history[-200:]
                    logger.info(
                        f"[Trader] Realized slippage: expected={expected_out} "
                        f"actual={actual_received} ({realized_pct:+.3f}%)"
                    )
            except Exception as e:
                logger.debug(f"[Trader] post-swap balance check failed: {e}")
        return True

    async def _send_transaction(self, swap_tx_b64: str) -> bool:
        """
        Send a signed transaction AND wait for on-chain confirmation.

        Returns True only when the transaction is confirmed with no error.
        Returns False if:
          - sendTransaction was rejected by the RPC node
          - The transaction was included but failed (slippage, compute, etc.)
          - Confirmation timed out (likely dropped by network)
        """
        try:
            from solders.keypair import Keypair
            from solders.transaction import VersionedTransaction

            keypair = Keypair.from_base58_string(self.private_key)
            tx_bytes = base64.b64decode(swap_tx_b64)
            unsigned_tx = VersionedTransaction.from_bytes(tx_bytes)
            # solders >=0.20: VersionedTransaction has no .sign() method.
            # Construct a signed transaction from message + signers instead.
            signed_tx = VersionedTransaction(unsigned_tx.message, [keypair])

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base64.b64encode(bytes(signed_tx)).decode("utf-8"),
                    {"encoding": "base64", "skipPreflight": False}
                ]
            }
            result = await self._post_rpc(payload, total_timeout=30.0)
            if not result:
                logger.error(f"TX send failed across all RPC endpoints")
                return False
            if "error" in result:
                logger.error(f"TX error: {result['error']}")
                return False
            sig = result.get("result", "")
            if not sig:
                logger.error(f"TX accepted but no signature returned: {result}")
                return False
            logger.info(f"TX sent: {sig}")
            # Wait for on-chain confirmation.  Without this, sendTransaction
            # returning success only means the RPC accepted the tx — the tx
            # could still fail to land (priority fee too low, blockhash
            # expired) or land with an error (slippage, compute exceeded).
            return await self._await_tx_confirmation(sig)
        except ImportError:
            logger.warning("solders not installed — run: pip install solders")
            return False
        except Exception as e:
            logger.error(f"Transaction error: {e}")
            return False

    async def _await_tx_confirmation(self, signature: str,
                                       max_wait_seconds: float = 45.0,
                                       poll_interval: float = 1.5) -> bool:
        """
        Poll getSignatureStatuses until the tx is confirmed or finalized,
        or until max_wait_seconds elapses.  Returns True only on confirmed
        success.  False on tx-level error or timeout.
        """
        deadline = time.time() + max_wait_seconds
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignatureStatuses",
                "params": [[signature], {"searchTransactionHistory": True}],
            }
            data = await self._post_rpc(payload, total_timeout=10.0) or {}
            statuses = (data.get("result") or {}).get("value") or []
            status = statuses[0] if statuses else None
            if status:
                err = status.get("err")
                confirmation_status = status.get("confirmationStatus")
                if err is not None:
                    self._exec_stats["confirm_errors"] += 1
                    logger.error(
                        f"[Trader] TX {signature[:12]}… failed on-chain: "
                        f"{err} (after {attempt} polls)"
                    )
                    return False
                if confirmation_status in ("confirmed", "finalized"):
                    logger.info(
                        f"[Trader] TX {signature[:12]}… {confirmation_status} "
                        f"(after {attempt} polls)"
                    )
                    return True
            await asyncio.sleep(poll_interval)
        self._exec_stats["confirm_timeouts"] += 1
        logger.error(
            f"[Trader] TX {signature[:12]}… confirmation timeout after "
            f"{max_wait_seconds}s — assuming dropped"
        )
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
