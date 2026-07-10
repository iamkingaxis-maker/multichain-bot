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

# ── Jupiter ULTRA (MEV-protected routing) — 2026-06-02, for the live measurement
# probe. Ultra builds the swap tx server-side (RTSE slippage + protected routing)
# and LANDS it through Jupiter's own protected infra (no public-mempool send), so
# it is NOT sandwich-able the way the standard quote+swap+sendTransaction path is.
# Flow: GET /ultra/v1/order -> sign the returned tx -> POST /ultra/v1/execute.
# HARD-GATED behind USE_JUPITER_ULTRA (default off) AND only ever runs with a
# private key (live mode) — dormant in paper. Paid key uses api.jup.ag; free tier
# uses lite-api.jup.ag. See docs/superpowers/specs/2026-06-02-live-measurement-probe-design.md.
USE_JUPITER_ULTRA = _os.environ.get("USE_JUPITER_ULTRA", "0").strip().lower() in ("1", "true", "yes", "on")
_ULTRA_BASE = "https://api.jup.ag" if _JUPITER_API_KEY else "https://lite-api.jup.ag"
JUPITER_ULTRA_ORDER_API = f"{_ULTRA_BASE}/ultra/v1/order"
JUPITER_ULTRA_EXECUTE_API = f"{_ULTRA_BASE}/ultra/v1/execute"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Absolute glitch ceiling for the paper-sell sanity guard (2026-06-13): a sell
# whose price implies a gain beyond this MULTIPLE of entry is a feed glitch, even
# if a second feed "agrees" (both can read the same bad pair/units — RAGEGUY
# printed +485,336% = 4,850x and booked +$242,668 because the cross-check trusted
# the agreement). No position on our hold timescale sustains this; abort the sell
# (a genuine moonshot re-prices next tick, a glitch reverts). Mirrors the per-bot
# exit_price_guard's OHLC-high rejection on the legacy path.
_GLITCH_CEILING_X = 50.0


# ── Jupiter Ultra pure helpers (no I/O — unit-tested in tests/test_jupiter_ultra.py) ──
def exit_slippage_bps_for_attempt(is_urgent: bool, attempt: int) -> int:
    """Slippage cap for a live SELL by retry attempt (2026-07-05 tail audit).

    A reverted exit means price already moved past the cap — re-quoting at the
    SAME cap keeps failing while the token crashes, and after 3 failures the
    position rides the dump ('remains open, will retry on next tick'). Urgent
    exits (stops/bails/floors/manual) therefore ESCALATE: 300 -> 800 -> 1500
    bps; normal sells stay tight (100/100/300 — the 3rd attempt loosens a
    hair so profit-taking can't wedge either). EXIT_SLIP_ESCALATION=off
    restores the flat legacy caps (300 urgent / 100 normal). Values beyond
    the schedule clamp to the last step. Pure; never raises."""
    try:
        if os.environ.get("EXIT_SLIP_ESCALATION", "on").strip().lower() in (
                "off", "0", "false", "no"):
            return 300 if is_urgent else 100
    except Exception:
        pass
    sched = (300, 800, 1500) if is_urgent else (100, 100, 300)
    try:
        a = max(0, int(attempt))
    except (TypeError, ValueError):
        a = 0
    return sched[min(a, len(sched) - 1)]


def build_ultra_order_params(input_mint: str, output_mint: str, amount: int,
                             taker: str, slippage_bps: "Optional[int]" = None) -> dict:
    """Query params for GET /ultra/v1/order. Ultra estimates slippage itself (RTSE)
    when slippageBps is omitted; pass it only to cap. amount is in atomic units."""
    p = {"inputMint": input_mint, "outputMint": output_mint,
         "amount": int(amount), "taker": taker}
    if slippage_bps is not None:
        p["slippageBps"] = int(slippage_bps)
    return p


def parse_ultra_order(resp: "Optional[dict]") -> dict:
    """Normalize an Ultra /order response. Returns {ok, transaction, request_id,
    out_amount, in_amount, router, slippage_bps, reason}. ok=False if unusable."""
    if not isinstance(resp, dict):
        return {"ok": False, "reason": "no_response"}
    tx = resp.get("transaction")
    rid = resp.get("requestId")
    if not tx or not rid:
        return {"ok": False, "reason": resp.get("error") or "missing_tx_or_requestId",
                "request_id": rid}
    def _i(v):
        try: return int(v)
        except (TypeError, ValueError): return 0
    return {"ok": True, "transaction": tx, "request_id": rid,
            "out_amount": _i(resp.get("outAmount")), "in_amount": _i(resp.get("inAmount")),
            "router": resp.get("router") or resp.get("swapType"),
            "slippage_bps": resp.get("slippageBps"),
            # priceImpactPct = quote's estimated price impact (fraction, e.g. "0.0287").
            # Surfaced first-class for the Tier-B in-flight fill-quality abort. May be None.
            "price_impact_pct": resp.get("priceImpactPct"),
            # prioritizationFeeLamports = the REAL priority fee Ultra set on the built tx
            # (~175k lamports typical, NOT the 1M-2M cap). Promoted so telemetry / paper-fee
            # calibration can read the actual cost instead of assuming the cap. May be None.
            "priority_fee_lamports": resp.get("prioritizationFeeLamports")}


def parse_ultra_execute(resp: "Optional[dict]") -> dict:
    """Normalize an Ultra /execute response. Returns {ok, status, signature,
    slippage_bps, error, code}. ok=True only on status 'Success'."""
    if not isinstance(resp, dict):
        return {"ok": False, "status": None, "signature": None, "reason": "no_response"}
    status = resp.get("status")
    sig = resp.get("signature")
    ok = (str(status).lower() == "success") and bool(sig)
    return {"ok": ok, "status": status, "signature": sig,
            "slippage_bps": resp.get("slippageBps"),
            "error": resp.get("error"), "code": resp.get("code")}


def _trim_ultra_order_resp(resp: "Optional[dict]") -> "Optional[dict]":
    """Trim an Ultra /order response to a few KEY debug fields (never the full
    blob — the transaction base64 is huge). Pure + defensive: None on bad input."""
    if not isinstance(resp, dict):
        return None
    rp = resp.get("routePlan")
    route_summary = None
    try:
        if isinstance(rp, list):
            route_summary = [
                (((step or {}).get("swapInfo") or {}).get("label")
                 or ((step or {}).get("swapInfo") or {}).get("ammKey"))
                for step in rp[:6]
            ]
    except Exception:
        route_summary = None
    return {
        "outAmount": resp.get("outAmount"),
        "inAmount": resp.get("inAmount"),
        "slippageBps": resp.get("slippageBps"),
        "priceImpactPct": resp.get("priceImpactPct"),
        "router": resp.get("router") or resp.get("swapType"),
        "requestId": resp.get("requestId"),
        "routePlan": route_summary,
        "prioritizationFeeLamports": resp.get("prioritizationFeeLamports"),
    }


def _trim_ultra_execute_resp(resp: "Optional[dict]") -> "Optional[dict]":
    """Trim an Ultra /execute response to KEY debug fields. Pure + defensive."""
    if not isinstance(resp, dict):
        return None
    return {
        "status": resp.get("status"),
        "signature": resp.get("signature"),
        "slippageBps": resp.get("slippageBps"),
        "error": resp.get("error"),
        "code": resp.get("code"),
        "totalInputAmount": resp.get("totalInputAmount"),
        "totalOutputAmount": resp.get("totalOutputAmount"),
    }


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
# 2026-06-08 persistence standard: paper positions now persist too, to a SEPARATE
# mode-namespaced file (a paper file must never be restored by a live process, nor
# vice-versa). Previously paper positions were ephemeral — every restart lost them
# and PerformanceTracker synthetic-closed them at 0% ("cancelled on restart"),
# which (a) corrupted strategy P&L and (b) HID real losses at breakeven, inflating
# fleet win-rate via loser-survivorship on every redeploy. Paper positions now
# survive restarts like the multi-bot fleet's do.
_OPEN_POSITIONS_PAPER_FILE = os.path.join(_DATA_DIR, "open_positions_paper.json")


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

        # Decimals cache (perf, free): mint decimals NEVER change, so cache them
        # permanently per (original-case) address. Pre-warmed when a token is armed
        # so the live fire path's _get_token_decimals is always a cache hit (no cold
        # getAccountInfo blocking the swap). Fail-safe: a miss just does the RPC.
        self._token_decimals_cache: Dict[str, int] = {}

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

        # Token-level stop-loss streak tracker — addr -> [list of stop_ts].
        # When 3+ stops on the same address within 4h, apply a 4h cooldown
        # (override the no-cooldown stop default). Pattern: RAGEGUY
        # 2026-05-14 had 4 stops in 4h, all losses. Single-stop rebuys stay
        # uncooldowned (the +$600 lifetime band).
        self._dip_stop_streak: Dict[str, list] = {}
        self._dip_stop_streak_path = os.path.join(
            os.environ.get("DATA_DIR", "/data"), "dip_stop_streak.json"
        )
        try:
            if os.path.exists(self._dip_stop_streak_path):
                with open(self._dip_stop_streak_path) as f:
                    raw = json.load(f) or {}
                self._dip_stop_streak = {k: list(v) for k, v in raw.items()}
        except Exception:
            self._dip_stop_streak = {}

        pass  # daily buy limit removed — entry quality handles repeat buys

        # Optional Axiom auth — registered externally for Axiom-based price lookups
        self._axiom_auth = None

        # Optional Axiom real-time price feed (Phase 4)
        self._axiom_price_feed = None

        # Optional DexScreener real-time price feed (sub-second stop-loss accuracy)
        self._dex_price_feed = None

        # Optional Solana RPC + Jupiter price feed (0.5s, covers all pool types)
        self._rpc_price_feed = None

        # Optional Helius-WS on-chain pool price feed (sub-second, decodes
        # vault reserves directly — bypasses DexScreener indexer lag). Solves
        # the RAGEGUY 2026-05-15 issue where real pool pumped +13.5% but
        # indexed feed lagged so bot saw +1.1%.
        self._pool_price_feed = None

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
        # Persist in BOTH modes (2026-06-08); mode-namespaced so paper/live never
        # cross-restore. Paper used to early-return here (ephemeral) — that was the
        # source of the restart flush + loser-survivorship.
        _file = _OPEN_POSITIONS_FILE if self.private_key else _OPEN_POSITIONS_PAPER_FILE
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
            tmp = _file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, _file)
        except Exception as e:
            logger.warning(f"[Trader] _save_open_positions failed: {e}")

    def _restore_open_positions(self) -> None:
        """
        Restore open_positions from disk on startup.  Live-only.  Paired with
        reconcile_positions_on_startup which then validates each restored
        position against actual on-chain wallet holdings.
        """
        # Restore in BOTH modes (2026-06-08) from the mode-namespaced file. Live
        # restore is still paired with reconcile_positions_on_startup (on-chain
        # validation); paper just rebuilds the in-memory book.
        _file = _OPEN_POSITIONS_FILE if self.private_key else _OPEN_POSITIONS_PAPER_FILE
        if not os.path.exists(_file):
            logger.info("[Trader] No persisted open_positions to restore")
            return
        # Zombie-resurrection guard (2026-06-10): Railway deploys OVERLAP — the
        # new container loads this snapshot while the old one still serves; a
        # manual sell handled by the old container then gets overwritten by the
        # new container's stale book on its next save (MINER/ZOOMER were
        # manually sold TWICE and resurrected twice on a 12-deploy day). The
        # append-only trades log is authoritative: never restore a position
        # whose address has a MANUAL full-close sell AFTER its entry_time
        # (manual sells are user intent with no automatic re-trigger; TP
        # partials/remainders are untouched by this rule).
        # 2026-06-12 broadened (FTP: ONE buy -> TEN sell records across a
        # 10-deploy night; duplicate sells 70s apart booked to baseline_v1 AND
        # None = both overlap instances selling, then the stale book
        # resurrecting the position to die again, ~-$27 from one $57 position):
        # tombstone ANY known FULL-CLOSE sell, not just manual. TP partials and
        # other fraction sells never tombstone — riding remainders restore fine.
        _FULL_CLOSE_MARKERS = (
            "manual sell", "volume death", "pre-stop bail", "hard stop",
            "stop loss", "elite-exit", "post-tp1 trail", "pre-tp1 trail",
            "never-green", "fast-dud", "fast bail", "giveback floor",
        )
        _manual_close = {}
        try:
            with open(os.path.join(_DATA_DIR, "trades.json")) as f:
                _td = json.load(f)
            for t in (_td if isinstance(_td, list) else _td.get("trades", [])):
                a = (t.get("token_address") or t.get("address") or "").lower()
                if not a:
                    continue
                ts = t.get("time") or ""
                _why = (t.get("reason") or "").lower()
                if (t.get("type") == "sell"
                        and any(m in _why for m in _FULL_CLOSE_MARKERS)):
                    if ts >= _manual_close.get(a, ""):
                        _manual_close[a] = ts
                elif t.get("type") == "buy":
                    # a later re-buy legitimately reopens the token
                    if ts >= _manual_close.get(a, ""):
                        _manual_close.pop(a, None)
        except Exception:
            _manual_close = {}
        try:
            with open(_file) as f:
                payload = json.load(f)
            for d in payload.get("positions", []):
                _addr_l = (d.get("token_address") or "").lower()
                _mc_ts = _manual_close.get(_addr_l)
                if _mc_ts and (d.get("entry_time") or "") <= _mc_ts:
                    logger.warning(
                        f"[Trader] 🧟 ZOMBIE DROPPED on restore: "
                        f"{d.get('token_symbol')} fully closed at {_mc_ts} "
                        f"(deploy-overlap resurrection guard)")
                    continue
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
        # Cache hit (decimals never change) — keeps the live fire path off the RPC.
        try:
            _cached = self._token_decimals_cache.get(mint)
            if isinstance(_cached, int):
                return _cached
        except Exception:
            pass
        # NEGATIVE cache (2026-07-10 429-storm postmortem): a failed lookup must
        # NOT retry every tick. Restart + big armed set -> 50 prewarm misses/tick
        # x 4 RPC URLs = ~100 req/s -> 429 everywhere -> nothing ever caches ->
        # self-sustaining storm that starved the whole HTTP stack (polled=0,
        # fleet drought). Failures wait DECIMALS_NEG_TTL_SECS before retrying.
        try:
            _neg = getattr(self, "_token_decimals_neg", None)
            if _neg is None:
                _neg = self._token_decimals_neg = {}
            _ttl = float(os.getenv("DECIMALS_NEG_TTL_SECS", "600") or 600)
            _nts = _neg.get(mint)
            if _nts is not None and (time.time() - _nts) < _ttl:
                return 6  # recent failure: pump.fun fallback, no RPC
        except Exception:
            pass
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
                try:
                    self._token_decimals_cache[mint] = decimals  # permanent: never changes
                except Exception:
                    pass
                return decimals
        except Exception as e:
            logger.debug(f"[Trader] _get_token_decimals failed for {mint[:8]}…: {e}")
        # Failure: stamp the negative cache so the next DECIMALS_NEG_TTL_SECS
        # of lookups skip the RPC entirely (429-storm regression guard).
        try:
            _neg = getattr(self, "_token_decimals_neg", None)
            if _neg is None:
                _neg = self._token_decimals_neg = {}
            _neg[mint] = time.time()
            if len(_neg) > 4096:  # bound: drop oldest half
                for _k in sorted(_neg, key=_neg.get)[:2048]:
                    _neg.pop(_k, None)
        except Exception:
            pass
        return 6  # fallback: pump.fun convention (neg-cached above, retries after TTL)

    async def prewarm_decimals(self, mint: str) -> None:
        """Pre-populate the decimals cache for an ARMED token so the live fire path's
        _get_token_decimals is a cache hit (never blocks on a cold getAccountInfo).
        Idempotent + fail-safe: a hit is a no-op; any error is swallowed (the live path
        falls back to a fresh RPC). Free (one getAccountInfo, only on a cold miss)."""
        try:
            if not mint or mint in self._token_decimals_cache:
                return
            await self._get_token_decimals(mint)  # populates the cache on success
        except Exception:
            pass

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
        data = await self._post_rpc(payload, total_timeout=5.0)
        # Distinguish a FAILED read from a genuine zero. _post_rpc returns
        # None (or a dict carrying an "error" / no usable "result") on a
        # timeout, 429, or RPC error. Returning 0 in those cases made a
        # transient hiccup look like "0 tokens on chain" -> the sell path
        # (_execute_bot_sell_live) booked a phantom PAPER close on a REAL
        # live position, stranding the tokens in the wallet (2026-06-21 BOB).
        # A failed read MUST be -1 so the caller's `bal is None or bal < 0`
        # sentinel keeps the position OPEN and retries next tick.
        if not isinstance(data, dict) or data.get("error") is not None:
            return -1
        result = data.get("result")
        if not isinstance(result, dict):
            return -1
        accounts = result.get("value") or []
        if not accounts:
            return 0  # RPC succeeded; owner genuinely holds no account for this mint
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
        # 2026-05-18 — RE-ENABLED narrow 30-min same-token cooldown after
        # stop-out / vol-death. Universe recorder mining (n=2691, n=487
        # stop-out-rebuy events) showed:
        #   <15min post-stop: avg=-6.89%, WR=29.7%, stop-rate=58.4%
        #   15-30min:        avg=-2.50%, WR=44.1%
        #   30-60min:        avg=+5.50%, WR=71.4% (flips positive)
        #   1-2h:            avg=+4.37%, WR=80.0%
        # Compare to first entries: +7.28%/70.6% WR.
        #
        # The 30-min window is where the negative signal decays. This is
        # NOT a generic loss-cooldown (those were correctly removed today —
        # global N-stops-pause-trading patterns are bandaids). This is a
        # per-token, narrow, evidence-backed gate validating that re-entry
        # on the SAME token within 30 min is a different statistical
        # population (30% WR vs 70%).
        #
        # Mira 2026-05-17 was the trigger case: Buy #2 at 47 min post-stop
        # narrowly missed this window — data shows 30-60min is on the edge,
        # so future similar entries may still fire. Watch forward.
        if is_vol_death or is_stop_loss:
            self._dip_loss_cooldown[token_address.lower()] = [time.time(), 1800.0]
            self._save_dip_loss_cooldown()
            tag = "vol-death" if is_vol_death else "stop-loss"
            logger.info(
                f"[Trader] {tag} close on {token_address[:8]}… — 30-min same-token cooldown set"
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

    def register_pool_price_feed(self, feed):
        """Register the on-chain pool price feed (Helius WS, sub-second).

        Decodes vault reserves directly from accountNotification — fastest
        possible price source for Raydium AMM v4 and pump.fun bonding-curve
        pools. Falls back silently for unknown pool types (e.g. pumpswap),
        so this is purely additive on top of dex_price_feed / rpc_price_feed.
        """
        self._pool_price_feed = feed
        try:
            for _addr, _pos in self.open_positions.items():
                _pair = getattr(_pos, "pair_address", "") or ""
                if _pair:
                    feed.subscribe_token(_addr, pair_address=_pair)
        except Exception as _e:
            logger.warning(f"[Trader] pool feed re-subscribe error: {_e}")

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
                  entry_meta: Optional[dict] = None,
                  force_paper: bool = False):
        """Execute a buy order. force_paper=True routes to the PAPER sim even when a
        live key is present (C1, 2026-06-04 live-execution audit) — the single knob
        that lets any caller be neutralized so the live_probe allowlist is the only
        path to real money."""
        if os.environ.get("TRADING_PAUSED", "").lower() in ("true", "1", "yes"):
            logger.info(f"[Trader] Buy blocked — TRADING_PAUSED=true ({strategy}/{token_symbol})")
            return
        if self._dashboard_paused:
            logger.info(f"[Trader] Buy blocked — dashboard pause active ({strategy}/{token_symbol})")
            return
        # Trading-hours window (Central Time). 2026-05-14 update:
        # overnight cohort mining (mine_overnight_cohorts.py) found a
        # +$358 lifetime block in 3-6am CT (60-66% per-hour WR), so the
        # active window expands to 3am-5pm CT. The 8pm-2am CT bleeding
        # zone (-$300+ lifetime) stays closed. Prior 7am-5pm window
        # (commit 2026-05-12) captured 7am-5pm only. Default below
        # mirrors expected Railway env values; Railway env vars take
        # precedence. Only gates new buys; sells/TPs/stops continue.
        try:
            _start_h = int(os.environ.get("TRADING_START_HOUR_CT", "3"))
            _end_h = int(os.environ.get("TRADING_END_HOUR_CT", "17"))
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
        elif self.private_key and not force_paper:
            # C6 (2026-06-04 live-execution audit): FAIL-CLOSED in live. With a key
            # present and no STRATEGY_ALLOWLIST set, refuse direct trader.buy strategies
            # (the live_probe fleet path does NOT go through buy()). Paper (no key) keeps
            # allow-all so research is unaffected; force_paper buys pass to the paper sim.
            logger.critical(
                f"[Trader] LIVE buy blocked — STRATEGY_ALLOWLIST unset (fail-closed) "
                f"strategy='{strategy}' {token_symbol}")
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
                elif strategy.startswith("smart_follow"):
                    # smart_follow* has its own pool too (FollowCapitalManager,
                    # 2026-06-11) — own floor, own sweep ledger, no shared-book clip.
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
            # FollowCapital pool gate (2026-06-11): smart_follow* can only deploy
            # what its own pool has available — losses shrink firepower, sweeps
            # bank the excess, the legacy shared book is untouched.
            if strategy.startswith("smart_follow") and getattr(self, "follow_capital", None):
                if not self.follow_capital.can_open(position_size_usd):
                    logger.info(
                        f"[Trader] FollowCapital blocked {token_symbol}: need "
                        f"${position_size_usd:.0f}, available "
                        f"${self.follow_capital.available():.2f}")
                    return
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

            # ── filter_quad ENFORCED 2026-05-14 (with big-buyer carve-out) ───
            # 4-component combo identified by combinatorial optimization on the
            # 2026-05-03 baseline-mode dataset (n=223). Promoted from shadow
            # after 1044-trade validation:
            #   Stability: PASS > BLOCK on 8/9 days (89%), net +$0.35/trade
            #   Post-May-12 (current production): saves $14/day before carve-out
            #   Recent 7d (May 2-9): saves $83 (passes lose more than blocks)
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
            # Carve-out: rescue if liq_velocity_h1_usd_per_txn >= 115 (same
            # big-buyer carve-out applied to seller-trio filters on 2026-05-09).
            # On post-May-12 sample this rescues COPPERINU/HANTA/BUFO/Crack
            # winners while keeping the 11 blocked losers (net +$17.91 vs
            # naive enforcement +$14.04 — carve-out adds $3.87 in winners
            # rescued).
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
                # Carve-out check: big-buyer rescue.
                _q_carve_rescued = False
                if _q_block_reasons:
                    _q_lvh1 = entry_meta.get("liq_velocity_h1_usd_per_txn")
                    if isinstance(_q_lvh1, (int, float)) and not isinstance(_q_lvh1, bool):
                        if _q_lvh1 >= 115:
                            _q_carve_rescued = True
                            logger.info(
                                f"[Trader] filter_quad RESCUED: {token_symbol} "
                                f"liq_velocity_h1={_q_lvh1:.1f}>=115 "
                                f"(big-buyer carve-out, would-block: {','.join(_q_block_reasons)})"
                            )
                _q_verdict = "BLOCK" if (_q_block_reasons and not _q_carve_rescued) else "PASS"
                entry_meta["filter_quad_verdict"] = _q_verdict
                entry_meta["filter_quad_block_reasons"] = _q_block_reasons
                # 2026-05-17 PM — DEMOTED to SHADOW. Volume-recovery context:
                # filter_quad was blocking ~25-40% of dip_buy signals at trader
                # level. With mcap_low gate locked at $80k, the trader cascade
                # is the only remaining volume lever. Demoting filter_quad to
                # SHADOW logs the would-block decision but does NOT abort the
                # buy — bot trades through. Watch entry_meta.filter_quad_verdict
                # forward: if SHADOW-BLOCK trades have materially worse WR than
                # SHADOW-PASS, repromote. Original ENFORCED path commented below.
                if _q_verdict == "BLOCK" and strategy == "dip_buy":
                    logger.info(
                        f"[Trader] filter_quad SHADOW would-block: {token_symbol} "
                        f"reasons={','.join(_q_block_reasons)}"
                    )
                    # return  # DEMOTED 2026-05-17 PM — see comment above

            # ── filter_top10_holder_band ENFORCED 2026-05-14 PM ───────────────
            # Insider-zone holder concentration: top 10 holders own 70-80% of
            # supply. Mined on n=107 paired (TRAIN -$1.08/tr, TEST -$1.21/tr,
            # 45% WR both periods). Stable held-out, large sample.
            #
            # Mechanism: tokens with top10 in the 70-80% band are deep enough
            # to suggest insider control but not so high (80%+) that the bag
            # is clearly held by a single committed wallet. The 70-80% band is
            # the "mid-insider" sweet spot where insider dumps are common.
            # [0,30) is clean distribution. [80+) is single-whale conviction
            # (62% WR, +$0.50/tr in mining — a different regime, do NOT block).
            #
            # Carve-out: rescue if liq_velocity_h1_usd_per_txn >= 115 (same
            # big-buyer carve-out as filter_quad/seller-trio). Validation on
            # post-May-12 production: blocks only 1 winner (COPPERINU +$0.69,
            # rescued by carve-out since lvh1=251).
            if entry_meta is not None and isinstance(entry_meta, dict):
                _t10b_block_reasons: List[str] = []
                _t10b_t10 = entry_meta.get("top10_holder_pct")
                if _t10b_t10 is not None:
                    try:
                        if 70.0 <= float(_t10b_t10) < 80.0:
                            _t10b_block_reasons.append(
                                f"top10_holder_pct={float(_t10b_t10):.2f}∈[70,80) "
                                f"(insider-zone band)"
                            )
                    except Exception:
                        pass
                _t10b_carve_rescued = False
                if _t10b_block_reasons:
                    _t10b_lvh1 = entry_meta.get("liq_velocity_h1_usd_per_txn")
                    if isinstance(_t10b_lvh1, (int, float)) and not isinstance(_t10b_lvh1, bool):
                        if _t10b_lvh1 >= 115:
                            _t10b_carve_rescued = True
                            logger.info(
                                f"[Trader] filter_top10_holder_band RESCUED: {token_symbol} "
                                f"liq_velocity_h1={_t10b_lvh1:.1f}>=115 (big-buyer carve-out)"
                            )
                _t10b_verdict = "BLOCK" if (_t10b_block_reasons and not _t10b_carve_rescued) else "PASS"
                entry_meta["filter_top10_holder_band_verdict"] = _t10b_verdict
                entry_meta["filter_top10_holder_band_block_reasons"] = _t10b_block_reasons
                # 2026-05-18 — high_activity_fast_path BYPASS REMOVED.
                # Mira (2026-05-17) was bought twice via this bypass path
                # despite 15-16 SHADOW filters saying BLOCK. The bypass let
                # breakthrough-trigger trades skip filter discipline, exactly
                # the architectural pattern that produced the 29% WR on $100k-$1M
                # microcaps vs April 28's 84% WR on $1M+ established tokens.
                # April 28 had no bypass — every trade ran the full filter
                # chain in a single pass. Reverting to that discipline.
                if _t10b_verdict == "BLOCK" and strategy == "dip_buy":
                    logger.info(
                        f"[Trader] BLOCKED by filter_top10_holder_band: {token_symbol} "
                        f"reasons={','.join(_t10b_block_reasons)}"
                    )
                    return

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
                # 2026-05-18 — fast-path BYPASS REMOVED (see filter_top10_holder_band).
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
                # 2026-05-18 — fast-path BYPASS REMOVED (see filter_top10_holder_band).
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
            # C1 (2026-06-04 audit): force_paper routes here even with a live key, so a
            # caller NOT on the live_probe allowlist never reaches the live swap below.
            if not self.private_key or force_paper:
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
                # On-chain pool feed: subscribes to vault accounts via Helius WS.
                # Requires pair_address; silently no-ops for unknown pool types.
                if self._pool_price_feed is not None and pair_address:
                    self._pool_price_feed.subscribe_token(
                        token_address, pair_address=pair_address,
                        pool_type=("pump amm" if "pump amm" in reason.lower() else ""),
                    )

                sol_amount = await self._usd_to_sol(position_size_usd)
                if sol_amount <= 0:
                    logger.error(f"Could not convert USD→SOL for {token_symbol} — buy aborted")
                    return

                # Get current price — pair-pinned to the pool we transact on.
                # Without pair_address, multi-pair tokens like PENGUIN
                # (pumpswap 0.004 vs raydium 0.16) yield wrong entry prices.
                # Graduation buys: fresh graduates aren't indexed yet — skip
                # and derive entry from the Jupiter quote below instead.
                current_price = await self._get_token_price(token_address, pair_address=pair_address)
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
                if strategy.startswith("smart_follow") and getattr(self, "follow_capital", None):
                    self.follow_capital.record_open(token_address, position_size_usd)
                elif strategy != "scalp":
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
                # 2026-06-08 persistence standard: persist the paper book so this
                # position survives a restart (was ephemeral -> flushed at 0%).
                self._save_open_positions()
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
            if strategy.startswith("smart_follow") and getattr(self, "follow_capital", None):
                self.follow_capital.record_open(token_address, position_size_usd)
            elif strategy != "scalp":
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
            if self._pool_price_feed is not None and pair_address:
                self._pool_price_feed.subscribe_token(
                    token_address, pair_address=pair_address,
                    pool_type=("pump amm" if "pump amm" in reason.lower() else ""),
                )

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

    async def sell(self, token_address: str, token_symbol: str, reason: str, pct: float = 1.0,
                   force_paper: bool = False):
        """Execute a sell order for a percentage of the position. force_paper=True routes
        to the PAPER sim even with a live key (C1, 2026-06-04 audit).

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
            if not self.private_key or force_paper:
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
                        # Pair-pinned: paper sells must price against the
                        # pool the position was opened on, not the
                        # highest-liq pair (which can be 30x+ different).
                        current_price = await self._get_token_price(
                            token_address,
                            pair_address=getattr(position, "pair_address", "") or "",
                        )
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
                        # ABSOLUTE glitch ceiling: a gain beyond _GLITCH_CEILING_X is a feed
                        # glitch even if DexScreener agrees (both feeds can read the same bad
                        # pair/units). This is the RAGEGUY hole — abort unconditionally; do
                        # NOT let the cross-check below "confirm" an impossible multiple.
                        if _gain_x > _GLITCH_CEILING_X:
                            logger.critical(
                                f"[Trader] ⛔ Paper sell ABORTED (glitch ceiling): {token_symbol} "
                                f"{_gain_x:.0f}x entry > {_GLITCH_CEILING_X:.0f}x — feed glitch, "
                                f"skipping sell (no phantom booked)")
                            return
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
                    if self._pool_price_feed is not None:
                        self._pool_price_feed.unsubscribe_token(token_address)
                    # Forward-collector: stamp outcome on the partial label
                    # written at scan time. SHADOW only.
                    try:
                        from feeds.forward_dataset_collector import get_collector
                        _entry_ts_iso = getattr(position, "entry_time", None)
                        if _entry_ts_iso and hasattr(_entry_ts_iso, "isoformat"):
                            _entry_ts_iso = _entry_ts_iso.isoformat()
                        _total_pnl = getattr(position, "total_pnl_usd", 0.0) or 0.0
                        _pnl_pct = (_total_pnl / max(getattr(position, "amount_usd", 20.0), 1.0)) * 100.0
                        get_collector().update_outcome(
                            token_address=token_address,
                            ts_iso=str(_entry_ts_iso) if _entry_ts_iso else "",
                            outcome_label=1 if _total_pnl > 0 else 0,
                            outcome_pnl_pct=_pnl_pct,
                        )
                        # Parallel update for buy-level snapshot (full entry_meta).
                        get_collector().update_buy_outcome(
                            token_address=token_address,
                            ts_iso=str(_entry_ts_iso) if _entry_ts_iso else "",
                            outcome_label=1 if _total_pnl > 0 else 0,
                            outcome_pnl_pct=_pnl_pct,
                        )
                    except Exception as _e:
                        logger.debug(f"[Trader] forward_collector update err: {_e}")
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

                if (getattr(position, "strategy", "").startswith("smart_follow")
                        and getattr(self, "follow_capital", None)):
                    _cost = usd_received - pnl
                    _ppct = (pnl / _cost * 100.0) if _cost > 0 else None
                    self.follow_capital.record_close(token_address, pct, pnl, pnl_pct=_ppct)
                elif getattr(position, "strategy", "") != "scalp":
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
            # URGENT-EXIT classification: stops/manual closes must LAND, not
            # revert. 2026-07-05 tail audit: bails/floors ("bail", "floor",
            # "velocity") were NOT classified urgent -> they sold with the 1%
            # cap into crashes -> revert -> 1-2s sleep -> re-quote lower x3 ->
            # after 3 failures the position RIDES THE CRASH ("remains open,
            # will retry on next tick") — a mechanical gap-through amplifier.
            _is_urgent_exit = (("stop" in _r) or ("manual" in _r)
                               or ("bail" in _r) or ("floor" in _r)
                               or ("velocity" in _r))
            _slip_bps = exit_slippage_bps_for_attempt(_is_urgent_exit, 0)

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
                # ESCALATING slippage on urgent-exit retries (2026-07-05 tail
                # audit): a revert means price already moved past the cap —
                # re-quoting at the SAME cap keeps failing while the token
                # crashes. Attempt schedule (urgent): 300 -> 800 -> 1500 bps;
                # normal sells keep 100/100/300. On a $25 probe position the
                # worst-case last-resort fill costs ~$3.75 vs riding a dump
                # unbounded. EXIT_SLIP_ESCALATION=off restores flat caps.
                _slip_bps = exit_slippage_bps_for_attempt(_is_urgent_exit, _attempt)
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
                if self._pool_price_feed is not None:
                    self._pool_price_feed.unsubscribe_token(token_address)
                # Forward-collector: stamp outcome on the partial label
                # written at scan time. SHADOW only.
                try:
                    from feeds.forward_dataset_collector import get_collector
                    _entry_ts_iso = getattr(position, "entry_time", None)
                    if _entry_ts_iso and hasattr(_entry_ts_iso, "isoformat"):
                        _entry_ts_iso = _entry_ts_iso.isoformat()
                    _total_pnl = getattr(position, "total_pnl_usd", 0.0) or 0.0
                    _pnl_pct = (_total_pnl / max(getattr(position, "amount_usd", 20.0), 1.0)) * 100.0
                    get_collector().update_outcome(
                        token_address=token_address,
                        ts_iso=str(_entry_ts_iso) if _entry_ts_iso else "",
                        outcome_label=1 if _total_pnl > 0 else 0,
                        outcome_pnl_pct=_pnl_pct,
                    )
                    # Parallel update for buy-level snapshot (full entry_meta).
                    get_collector().update_buy_outcome(
                        token_address=token_address,
                        ts_iso=str(_entry_ts_iso) if _entry_ts_iso else "",
                        outcome_label=1 if _total_pnl > 0 else 0,
                        outcome_pnl_pct=_pnl_pct,
                    )
                except Exception as _e:
                    logger.debug(f"[Trader] forward_collector update err: {_e}")
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

            if (getattr(position, "strategy", "").startswith("smart_follow")
                    and getattr(self, "follow_capital", None)):
                _cost = usd_received - pnl
                _ppct = (pnl / _cost * 100.0) if _cost > 0 else None
                self.follow_capital.record_close(token_address, pct, pnl, pnl_pct=_ppct)
            elif getattr(position, "strategy", "") != "scalp":
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

    async def _execute_swap_ultra(self, input_mint: str, output_mint: str, amount: int,
                                  slippage_bps: Optional[int] = None,
                                  buy_context: bool = False) -> dict:
        """MEV-protected swap via Jupiter Ultra (order -> sign -> execute). Jupiter
        builds AND lands the tx through its own protected infra (not the public
        mempool), so it is not sandwich-able like the standard quote+swap+send path.
        Returns {success, out_amount, signature, status, route, realized_slippage_pct,
        reason}. Only runs live (requires private key); dormant in paper. Flag-gated by
        USE_JUPITER_ULTRA at the call site.

        buy_context=True uses a SHORTER /order retry backoff (LIVE_BUY_ORDER_BACKOFF_S,
        default 0.3s) — buys are time-sensitive (the dip edge decays); the slower 1s/2s
        exponential backoff is preserved for the sell path (buy_context=False)."""
        result = {"success": False, "out_amount": 0, "signature": None, "status": None,
                  "route": None, "realized_slippage_pct": None, "reason": None,
                  # ── live-swap telemetry stamps (fail-open extras; ignored by callers
                  # that don't read them). Durations are monotonic-ms; counts are ints.
                  "in_amount": 0, "slippage_cap_bps": slippage_bps,
                  "order_duration_ms": None, "sign_duration_ms": None,
                  "execute_duration_ms": None,
                  "order_attempts": 0, "order_429_count": 0, "execute_429_count": 0,
                  "backoff_total_ms": 0.0,
                  "ultra_slippage_bps": None, "price_impact_pct": None,
                  "priority_fee_lamports": None,
                  "raw_order_response": None, "raw_execute_response": None}
        if not self.private_key:
            result["reason"] = "paper_mode"
            return result
        self._exec_stats["swaps_attempted"] += 1
        params = build_ultra_order_params(input_mint, output_mint, amount,
                                          self._get_public_key(), slippage_bps)
        # 1) ORDER — Jupiter builds the protected tx.
        order = None
        _t_order0 = time.monotonic()
        for attempt in range(3):
            result["order_attempts"] = attempt + 1
            try:
                async with aiohttp.ClientSession(headers=_JUPITER_HEADERS) as session:
                    async with session.get(JUPITER_ULTRA_ORDER_API, params=params,
                                           timeout=aiohttp.ClientTimeout(total=12)) as resp:
                        if resp.status == 200:
                            _oj = await resp.json()
                            order = parse_ultra_order(_oj)
                            try:
                                result["raw_order_response"] = _trim_ultra_order_resp(_oj)
                            except Exception:
                                pass
                            break
                        if resp.status == 429:
                            result["order_429_count"] += 1
                        logger.warning(f"[Ultra] order HTTP {resp.status} (attempt {attempt+1}/3)")
            except Exception as e:
                logger.warning(f"[Ultra] order error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                if buy_context:
                    # Buy path: short fixed backoff (time-sensitive). Fail-safe: any bad
                    # env value falls back to 0.3s; the retry count + 12s timeout unchanged.
                    try:
                        _buy_bo = float(os.environ.get("LIVE_BUY_ORDER_BACKOFF_S", "0.3"))
                        if _buy_bo < 0:
                            _buy_bo = 0.3
                    except (TypeError, ValueError):
                        _buy_bo = 0.3
                    result["backoff_total_ms"] += _buy_bo * 1000.0
                    await asyncio.sleep(_buy_bo)
                else:
                    _bo = 2 ** attempt  # sell path: unchanged 1s/2s backoff
                    result["backoff_total_ms"] += _bo * 1000.0
                    await asyncio.sleep(_bo)
        result["order_duration_ms"] = round((time.monotonic() - _t_order0) * 1000, 1)
        if not order or not order.get("ok"):
            self._exec_stats["quote_failures"] += 1
            result["reason"] = (order or {}).get("reason", "order_failed")
            return result
        result["out_amount"] = order["out_amount"]
        result["in_amount"] = order.get("in_amount") or 0
        result["route"] = order.get("router")
        result["ultra_slippage_bps"] = order.get("slippage_bps")
        result["price_impact_pct"] = order.get("price_impact_pct")
        # Real priority fee Ultra set on the built tx (~175k lamports, not the cap).
        # Telemetry/paper-fee calibration reads this; pure surface, no behavior change.
        result["priority_fee_lamports"] = order.get("priority_fee_lamports")
        # ── Tier-B in-flight fill-quality abort (BUY only). The built order is in hand but
        # NOT yet signed/sent. If LIVE_FILL_QUALITY_MODE != off AND the quote's
        # priceImpactPct*100 exceeds LIVE_FILL_QUALITY_MAX_IMPACT_PCT (default 2.0), bail
        # BEFORE signing — costs $0, zero MEV exposure. Exits NEVER abort (don't trap a
        # position). FAIL-OPEN: any missing/non-numeric impact or error -> proceed as today.
        try:
            _fq_mode = os.environ.get("LIVE_FILL_QUALITY_MODE", "shadow").strip().lower()
            if buy_context and _fq_mode in ("shadow", "enforce"):
                _imp_raw = order.get("price_impact_pct")
                _imp_pct = None
                if _imp_raw is not None:
                    _imp_pct = float(_imp_raw) * 100.0  # raises -> caught -> fail-open
                if _imp_pct is not None:
                    try:
                        _ceil = float(os.environ.get("LIVE_FILL_QUALITY_MAX_IMPACT_PCT", "2.0"))
                    except (TypeError, ValueError):
                        _ceil = 2.0
                    if _imp_pct > _ceil:
                        if _fq_mode == "enforce":
                            logger.warning(
                                "[Ultra] fill-quality ABORT (enforce): impact=%.3f%% > %.3f%% "
                                "(in=%s out=%s) — $0 abort, not signed",
                                _imp_pct, _ceil, input_mint, output_mint)
                            result["reason"] = "fill_quality_impact"
                            return result
                        # shadow: log the would-block, DO NOT abort.
                        logger.info(
                            "[Ultra] fill-quality would-ABORT (shadow): impact=%.3f%% > %.3f%% "
                            "(in=%s out=%s)", _imp_pct, _ceil, input_mint, output_mint)
        except Exception as e:
            logger.debug("[Ultra] fill-quality gate error (%s) — fail-open (proceed)", e)
        # 2) SIGN the returned tx (same solders pattern as _send_transaction).
        _t_sign0 = time.monotonic()
        try:
            from solders.keypair import Keypair
            from solders.transaction import VersionedTransaction
            keypair = Keypair.from_base58_string(self.private_key)
            unsigned = VersionedTransaction.from_bytes(base64.b64decode(order["transaction"]))
            signed = VersionedTransaction(unsigned.message, [keypair])
            signed_b64 = base64.b64encode(bytes(signed)).decode("utf-8")
        except Exception as e:
            logger.error(f"[Ultra] sign error: {e}")
            self._exec_stats["swap_failures"] += 1
            result["reason"] = f"sign_error:{e}"
            return result
        result["sign_duration_ms"] = round((time.monotonic() - _t_sign0) * 1000, 1)
        # 3) EXECUTE — Jupiter lands it through protected infra.
        _t_exec0 = time.monotonic()
        try:
            async with aiohttp.ClientSession(headers=_JUPITER_HEADERS) as session:
                payload = {"signedTransaction": signed_b64, "requestId": order["request_id"]}
                async with session.post(JUPITER_ULTRA_EXECUTE_API, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 429:
                        result["execute_429_count"] += 1
                    _ej = await resp.json() if resp.status == 200 else None
                    ex = parse_ultra_execute(_ej)
                    try:
                        result["raw_execute_response"] = _trim_ultra_execute_resp(_ej)
                    except Exception:
                        pass
        except Exception as e:
            result["execute_duration_ms"] = round((time.monotonic() - _t_exec0) * 1000, 1)
            logger.error(f"[Ultra] execute error: {e}")
            self._exec_stats["swap_failures"] += 1
            result["reason"] = f"execute_error:{e}"
            return result
        result["execute_duration_ms"] = round((time.monotonic() - _t_exec0) * 1000, 1)
        result["status"] = ex.get("status")
        result["signature"] = ex.get("signature")
        if not ex.get("ok"):
            self._exec_stats["swap_failures"] += 1
            result["reason"] = ex.get("error") or f"status:{ex.get('status')}"
            return result
        self._exec_stats["successful_swaps"] += 1
        result["success"] = True
        # Ultra returns its own realized slippageBps — convert to % for the probe instrument.
        sb = ex.get("slippage_bps")
        if isinstance(sb, (int, float)):
            result["realized_slippage_pct"] = round(sb / 100.0, 4)
            result["ultra_slippage_bps"] = sb
            self._last_realized_slippage_pct = result["realized_slippage_pct"]
        logger.info(f"[Ultra] swap ok sig={ex.get('signature')} route={result['route']} "
                    f"slip={result['realized_slippage_pct']}%")
        return result

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

    async def send_sol_transfer(self, to_addr: str, lamports: int,
                                signer_b58: Optional[str] = None) -> Optional[str]:
        """Plain System Program SOL transfer hot->cold (profit sweep). Returns the
        CONFIRMED tx signature or None on any failure. The ONLY non-swap outbound
        transfer the bot makes — loud + confirmation-gated. The caller validates
        destination, caps the amount, and handles dry-run BEFORE this is ever called.

        signer_b58: explicit signing key. Defaults to self.private_key (live mode). The
        sweep-TEST passes the env key directly so it can sign ONE capped transfer while
        the trader's self.private_key stays empty -> the fleet's buy path stays keyless
        (100% paper). Decouples 'sweep can sign' from 'fleet trades live'."""
        key = signer_b58 or self.private_key
        if not key:
            logger.error("[Sweep] no signing key — transfer refused")
            return None
        try:
            import base64 as _b64
            from solders.keypair import Keypair
            from solders.pubkey import Pubkey
            from solders.system_program import transfer, TransferParams
            from solders.message import MessageV0
            from solders.transaction import VersionedTransaction
            from solders.hash import Hash
            kp = Keypair.from_base58_string(key)
            payer = kp.pubkey()
            to_pk = Pubkey.from_string(to_addr)
            if to_pk == payer:
                logger.critical("[Sweep] refusing transfer to self — abort")
                return None
            bh = await self._post_rpc({"jsonrpc": "2.0", "id": 1,
                                       "method": "getLatestBlockhash",
                                       "params": [{"commitment": "finalized"}]})
            blockhash = (((bh or {}).get("result") or {}).get("value") or {}).get("blockhash")
            if not blockhash:
                logger.error("[Sweep] no blockhash — abort")
                return None
            ix = transfer(TransferParams(from_pubkey=payer, to_pubkey=to_pk,
                                         lamports=int(lamports)))
            msg = MessageV0.try_compile(payer, [ix], [], Hash.from_string(blockhash))
            tx = VersionedTransaction(msg, [kp])
            raw = _b64.b64encode(bytes(tx)).decode("utf-8")
            res = await self._post_rpc({"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                                        "params": [raw, {"encoding": "base64",
                                                         "skipPreflight": False}]},
                                       total_timeout=30.0)
            if not res or "error" in res:
                logger.error(f"[Sweep] send failed: {(res or {}).get('error')}")
                return None
            sig = res.get("result", "")
            if not sig:
                return None
            logger.critical(f"[Sweep] transfer sent sig={sig} — awaiting confirmation")
            return sig if await self._await_tx_confirmation(sig) else None
        except Exception as e:
            logger.error(f"[Sweep] transfer error: {e}")
            return None

    async def execute_profit_sweep(self, dry_run: bool = True,
                                   max_usd: Optional[float] = None) -> dict:
        """Fire ONE profit sweep using this trader's live primitives. PLAN-then-SEND:
        the tested core.profit_sweeper guard logic (balance / USD cap / fail-closed
        destination) runs as a dry-run plan; only if a LIVE send is requested AND the
        plan cleared every guard do we execute the async transfer. The manual $5 test
        calls this with the default cap. Loud; no-op-safe (returns a structured dict)."""
        import os as _os
        from core import profit_sweeper as _ps
        if max_usd is None:
            max_usd = _ps.test_cap_usd()
        dest = _os.environ.get("PROFIT_WALLET_ADDRESS", "")
        bal = await self._get_sol_balance(force=True)
        sol_price = await self._get_token_price(SOL_MINT)
        sweeper = _ps.ProfitSweeper(
            get_balance_sol=lambda: (bal if isinstance(bal, (int, float)) and bal >= 0 else None),
            send_transfer=lambda d, l: None,  # unused — async send handled below
            get_sol_price_usd=lambda: (sol_price if sol_price and sol_price > 0 else None),
            configured_dest=dest, hot_addr=self._get_public_key(),
        )
        # Plan: runs ALL guards (balance fetch, $ cap, fail-closed destination).
        # ignore_threshold=True because the $5 test is intentionally below the
        # production SWEEP_THRESHOLD_SOL — the hard USD cap is the guard here.
        plan = sweeper.sweep_once(dry_run=True, max_usd=max_usd, ignore_threshold=True)
        if dry_run or not plan.get("dry_run"):
            return plan  # dry-run requested, OR a guard blocked the send
        sig = await self.send_sol_transfer(plan["dest"], plan["lamports"])
        if not sig:
            return {"sent": False, "reason": "transfer_failed", "amount_sol": plan["amount_sol"]}
        return {"sent": True, "amount_sol": plan["amount_sol"], "lamports": plan["lamports"],
                "dest": plan["dest"], "sig": sig}

    def _sweep_state_file(self):
        import os as _os
        from pathlib import Path as _Path
        return _Path(_os.environ.get("DATA_DIR") or "/data") / ".profit_sweep_state.json"

    def _load_sweep_state_once(self) -> None:
        """Load the persisted sweep state (floor high-water + last-sweep wall-clock)
        ONCE per process. Deploy-amnesia fix (2026-06-13): without this, the #6
        fat-finger floor-drop guard resets to the current (possibly fat-fingered)
        floor on every Railway redeploy, and the hourly interval re-arms to 'due' —
        so a floor DROP + the env-change redeploy that applies it together defeat
        the guard, and sweeps fire ~once per deploy instead of hourly."""
        if getattr(self, "_sweep_state_loaded", False):
            return
        self._sweep_state_loaded = True
        try:
            import json as _json
            p = self._sweep_state_file()
            if p.exists():
                d = _json.loads(p.read_text())
                self._floor_hwm_usd = float(d.get("floor_hwm_usd", 0.0) or 0.0)
                self._last_sweep_ts = float(d.get("last_sweep_ts", 0.0) or 0.0)
        except Exception as e:
            logger.error(f"[Sweep] state load failed: {e}")

    def _persist_sweep_state(self) -> None:
        try:
            import json as _json
            p = self._sweep_state_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_json.dumps({
                "floor_hwm_usd": float(getattr(self, "_floor_hwm_usd", 0.0) or 0.0),
                "last_sweep_ts": float(getattr(self, "_last_sweep_ts", 0.0) or 0.0)}))
        except Exception as e:
            logger.error(f"[Sweep] state persist failed: {e}")

    async def maybe_auto_sweep(self) -> None:
        """PRODUCTION auto profit-sweep (LIVE only). Keeps the hot wallet at the
        working-capital floor (USD-pegged) by sweeping ALL idle SOL above it to the
        cold wallet, at most once per min-interval, when the excess clears the min
        increment ($5). This is the user's policy: keep $X baseline, sweep everything
        above it. No-op in paper (no key). Best-effort — never raises into the loop.
        Uses self.private_key (the live key, present ONLY when PAPER_MODE=false), so
        it cannot move money in paper mode. Gated: PROFIT_SWEEP_ENABLED + a floor set;
        DRY-RUN by default (PROFIT_SWEEP_DRY_RUN=1) — logs intent until set false."""
        import os as _os
        import time as _time
        from core import profit_sweeper as _ps
        if not self.private_key:
            return  # paper -> no key -> no-op (cannot move money)
        if not _ps.enabled():
            return
        self._load_sweep_state_once()   # #1 deploy-amnesia: durable HWM + interval
        now = _time.time()              # wall-clock so the hourly interval survives restarts
        # #5 opportunistic (2026-06-13): a big realized win can be banked between
        # hourly sweeps to shrink the giveback window. The cheaper check-interval
        # gates RPC; the full sweep still fires hourly. opp=0 -> hourly-only (unchanged).
        _opp = _ps.opportunistic_usd()
        _full_due = (now - getattr(self, "_last_sweep_ts", 0.0)) >= _ps.min_interval_secs()
        _check_due = (now - getattr(self, "_last_sweep_check_ts", 0.0)) >= _ps.check_interval_secs()
        if not _full_due and not (_opp > 0 and _check_due):
            return
        self._last_sweep_check_ts = now
        try:
            dest = _os.environ.get("PROFIT_WALLET_ADDRESS", "")
            hot = self._get_public_key()
            if not _ps.validate_destination(dest, hot, dest):
                logger.error("[Sweep] AUTO: bad/mismatched destination — skip (fail-closed)")
                return
            price = await self._get_token_price(SOL_MINT)
            bal = await self._get_sol_balance(force=True)
            if not isinstance(bal, (int, float)) or bal < 0:
                return
            floor_usd = _ps.working_floor_usd()
            # #6 floor high-water (PERSISTED via #1 fix): track the highest floor ever
            # configured so a later fat-finger DROP (2000->200) is refused. Persist a
            # RAISE immediately so the env-change redeploy that applies a later drop
            # cannot reset the high-water first (the deploy-amnesia hole).
            _prev_hwm = float(getattr(self, "_floor_hwm_usd", 0.0) or 0.0)
            _fhwm = max(_prev_hwm, float(floor_usd or 0.0))
            self._floor_hwm_usd = _fhwm
            if _fhwm > _prev_hwm:
                self._persist_sweep_state()
            _floor_sol_ovr = _ps.floor_sol() or None  # #3 SOL-native floor if set
            d = _ps.auto_sweep_decision(
                bal, price, floor_usd, _ps.gas_buffer_sol(), _ps.min_increment_usd(),
                floor_sol_override=_floor_sol_ovr, floor_hwm_usd=_fhwm,
                floor_price_buffer_frac_v=_ps.floor_price_buffer_frac())  # #4 over-sweep haircut
            # #2 sub-floor alert (no silent throughput decay): the hot wallet has
            # dropped BELOW the working floor — banked profit is in cold and won't
            # auto-replenish. Loud, once per interval.
            if d.get("below_floor"):
                logger.critical(f"[Sweep] SUB-FLOOR: hot balance {bal:.4f} SOL below "
                                f"working floor ${floor_usd:.0f} — throughput reduced; "
                                f"manual re-fund needed (swept profit is in cold).")
            if not d.get("should_sweep"):
                logger.info(f"[Sweep] AUTO no-op: {d.get('reason')} "
                            f"(bal={bal:.4f} SOL, floor=${floor_usd:.0f})")
                return
            # If only the opportunistic path is due (not the full hourly), require the
            # opportunistic flag — else wait for the hourly fire.
            if not _full_due and not d.get("opportunistic"):
                return
            self._last_sweep_ts = now  # claim the hourly interval now that we'll sweep
            self._persist_sweep_state()  # #1: durable so the interval survives a restart
            if _ps.dry_run_default():
                logger.critical(f"[Sweep] AUTO DRY-RUN: would sweep {d['sweepable_sol']:.4f} SOL "
                                f"(~${d['sweepable_usd']:.2f}) -> {dest} (floor ${floor_usd:.0f})"
                                + ("" if not d.get("opportunistic") else " [opportunistic]")
                                + ("" if not d.get("clamped") else " [clamped to max-per-sweep]"))
                return
            # #1 commingled-wallet ack: a LIVE sweep banks fleet-aggregate profit
            # from a shared wallet (losers eat winners). Refuse unless the operator
            # has confirmed the live set is a single isolated config.
            if not _ps.single_config_ack():
                logger.critical("[Sweep] AUTO LIVE REFUSED: SWEEP_SINGLE_CONFIG_ACK not set "
                                "— a commingled live fleet sweep banks net-survival, not a "
                                "single bot's alpha. Isolate to one live config + set the ack.")
                return
            # #4 over-sweep guard (2026-06-17, HARDENED): a LIVE USD-pegged floor is
            # re-converted to SOL at the live price every cycle -> a transiently-HIGH
            # price tick shrinks the kept floor and authorizes a LARGER sweep; when the
            # price reverts down the SOL left is worth < the USD floor -> SUB-FLOOR drain
            # (the 06-17 ~$330 over-sweep). The ONLY price-risk-free fix is a SOL-NATIVE
            # floor (WORKING_CAPITAL_FLOOR_SOL). A bare SWEEP_MAX_PER_SWEEP_USD is a
            # blast-radius cap, NOT price-risk protection, so it ALONE no longer satisfies
            # this guard. A live USD-only floor now REQUIRES an explicit price-risk ack
            # (SWEEP_PRICE_RISK_ACK) AND a bound — either the stressed-price haircut
            # (SWEEP_FLOOR_PRICE_BUFFER_FRAC) or a per-sweep USD cap (SWEEP_MAX_PER_SWEEP_USD).
            if not _floor_sol_ovr:
                _has_bound = (_ps.floor_price_buffer_frac() > 0) or (_ps.max_per_sweep_usd() > 0)
                if not (_ps.price_risk_ack() and _has_bound):
                    logger.critical(
                        "[Sweep] AUTO LIVE REFUSED: USD-pegged floor carries SOL-price "
                        "over-sweep risk. Set WORKING_CAPITAL_FLOOR_SOL (SOL-native, no "
                        "price risk) — OR, to keep a USD floor, set SWEEP_PRICE_RISK_ACK=1 "
                        "AND a bound (SWEEP_FLOOR_PRICE_BUFFER_FRAC or SWEEP_MAX_PER_SWEEP_USD). "
                        "A bare SWEEP_MAX_PER_SWEEP_USD alone no longer satisfies this guard.")
                    return
            logger.critical(f"[Sweep] AUTO sweeping {d['sweepable_sol']:.4f} SOL "
                            f"(~${d['sweepable_usd']:.2f}) -> {dest} (keep ${floor_usd:.0f})")
            sig = await self.send_sol_transfer(dest, d["lamports"])
            if sig:
                logger.critical(f"[Sweep] AUTO confirmed ~${d['sweepable_usd']:.2f} sig={sig}")
            else:
                logger.error("[Sweep] AUTO transfer failed")
        except Exception as e:
            logger.error(f"[Sweep] AUTO error: {e}")

    async def maybe_fire_sweep_test(self) -> None:
        """One-shot BOOT trigger for the manual profit-sweep test (Option B). Fires at
        most ONCE per PROFIT_SWEEP_TEST_FIRE value, guarded by a persisted sentinel in
        the durable data dir so a redeploy can NEVER re-fire (no double-send of real
        money). =dry -> dry-run (logs the intended $5, moves nothing). =live -> one
        real $5-capped transfer. Unset/other -> no-op. Idempotency-first: the sentinel
        is CLAIMED before the send, so a crash mid-send can't cause a second send (a
        genuinely failed send is recovered by manually clearing the sentinel)."""
        import os as _os
        from pathlib import Path as _Path
        mode = (_os.environ.get("PROFIT_SWEEP_TEST_FIRE", "") or "").strip().lower()
        if mode not in ("dry", "live"):
            return
        data_dir = _os.environ.get("DATA_DIR") or "/data"
        try:
            sdir = _Path(data_dir)
            sdir.mkdir(parents=True, exist_ok=True)
            # Nonce lets us cleanly re-fire a test (bump PROFIT_SWEEP_TEST_NONCE) without
            # a stale sentinel blocking it. Each (mode,nonce) fires at most once.
            _nonce = (_os.environ.get("PROFIT_SWEEP_TEST_NONCE", "") or "").strip()
            sentinel = sdir / f".profit_sweep_test_fired_{mode}{('_' + _nonce) if _nonce else ''}"
            if sentinel.exists():
                logger.warning(f"[Sweep] BOOT test-fire '{mode}' already fired (sentinel) — skip")
                return
            # CLAIM before firing — guarantees no double-send across boots.
            sentinel.write_text(f"claimed mode={mode}\n")
        except Exception as e:
            logger.error(f"[Sweep] sentinel claim failed ({e}) — refusing test-fire (fail-closed)")
            return
        logger.critical(f"[Sweep] BOOT TEST-FIRE mode={mode} (one-shot, $5-capped)")
        try:
            result = await self._fire_sweep_test(dry_run=(mode == "dry"))
            logger.critical(f"[Sweep] BOOT TEST-FIRE result: {result}")
            try:
                sentinel.write_text(f"fired mode={mode} result={result}\n")
            except Exception:
                pass
            # Persist to a STABLE path so the result is readable via the dashboard GET
            # (log retention + scanner flood make the one-time boot line unreliable to scrape).
            try:
                import json as _json, time as _time
                (sdir / ".profit_sweep_last_test.json").write_text(_json.dumps(
                    {"ts": _time.time(), "mode": mode, "nonce": _nonce, "result": result}))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[Sweep] BOOT test-fire error: {e}")

    async def _fire_sweep_test(self, dry_run: bool = True) -> dict:
        """SELF-CONTAINED sweep test — proves the $5 transfer works BEFORE go-live,
        WITHOUT enabling the fleet. Reads SOLANA_PRIVATE_KEY from env directly (does
        NOT use self.private_key, so the trader's buy path stays keyless = the fleet
        stays 100% paper), derives the hot pubkey, checks its on-chain balance, runs
        the tested guard logic, and signs ONE capped transfer to the cold wallet.
        Hard backstops: $5 USD cap, a SOL-price sanity bound (a bad price can't
        inflate the cap), and an absolute 0.1-SOL ceiling regardless of price."""
        import os as _os
        from core import profit_sweeper as _ps
        key = (_os.environ.get("SOLANA_PRIVATE_KEY", "") or "").strip()
        if not key:
            return {"sent": False, "reason": "no_private_key_env"}
        try:
            from solders.keypair import Keypair
            hot = str(Keypair.from_base58_string(key).pubkey())
        except Exception as e:
            return {"sent": False, "reason": f"bad_key:{e}"}
        bal_resp = await self._post_rpc({"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                                         "params": [hot]})
        lam = (((bal_resp or {}).get("result") or {}).get("value"))
        if not isinstance(lam, (int, float)):
            return {"sent": False, "reason": "balance_fetch_failed", "hot": hot}
        bal_sol = lam / 1e9
        price = await self._get_token_price(SOL_MINT)
        # SOL-price sanity: a wrong (too-low) price would inflate the USD->SOL cap.
        if not (isinstance(price, (int, float)) and 30.0 <= price <= 2000.0):
            return {"sent": False, "reason": f"sol_price_implausible:{price}",
                    "hot_balance_sol": round(bal_sol, 6)}
        dest = _os.environ.get("PROFIT_WALLET_ADDRESS", "")
        sweeper = _ps.ProfitSweeper(
            get_balance_sol=lambda: bal_sol, send_transfer=lambda d, l: None,
            get_sol_price_usd=lambda: price, configured_dest=dest, hot_addr=hot)
        plan = sweeper.sweep_once(dry_run=True, max_usd=_ps.test_cap_usd(),
                                  ignore_threshold=True)
        plan["hot"] = hot
        plan["hot_balance_sol"] = round(bal_sol, 6)
        if dry_run or not plan.get("dry_run"):
            return plan  # dry-run requested, or a guard blocked it
        # Absolute hard ceiling (defense in depth): never send > 0.1 SOL from the test.
        ABS_MAX_LAMPORTS = int(0.1 * 1e9)
        lamports = min(int(plan["lamports"]), ABS_MAX_LAMPORTS)
        sig = await self.send_sol_transfer(plan["dest"], lamports, signer_b58=key)
        if not sig:
            return {"sent": False, "reason": "transfer_failed", "lamports": lamports}
        return {"sent": True, "lamports": lamports, "amount_sol": round(lamports / 1e9, 6),
                "dest": plan["dest"], "sig": sig}

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

    async def _get_token_price(self, token_address: str, pair_address: str = "") -> float:
        # When pair_address is provided, query the DexScreener pair endpoint
        # FIRST to anchor the quote to the exact pool we trade on. Multi-pair
        # tokens (PENGUIN: pumpswap 0.004 vs raydium 0.16, 41x apart) caused
        # entry_price to come from the wrong pair under highest-liquidity
        # selection, polluting paper P&L by 90%+.
        # Pair-pinned DexScreener pair lookup — runs FIRST when caller knows
        # the specific pool. This must come before Axiom/Jupiter cascade
        # because those return token-level aggregated prices that ignore
        # which pool the bot will actually transact on.
        if pair_address:
            try:
                url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        data = await resp.json(content_type=None)
                        pair = data.get("pair") or (data.get("pairs") or [None])[0]
                        if pair:
                            price = float(pair.get("priceUsd", 0) or 0)
                            if price > 0:
                                return price
            except Exception as e:
                logger.debug(f"[Trader] Pair-pinned price lookup failed for {pair_address[:10]}: {e}")

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
