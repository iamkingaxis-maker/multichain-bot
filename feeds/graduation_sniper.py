"""
Graduation Sniper
Subscribes to Solana program logs via WebSocket and detects pump.fun token
graduations (bonding curve completion → PumpSwap/Raydium AMM) in real-time.

Detection latency: ~200-500ms from block confirmation.
Edge over DexScreener-based bots: 30-90 seconds.
Edge over MEV/Jito bots: none — they're in the mempool.

Two detection paths:
  1. pump.fun logsSubscribe — "Instruction: Withdraw" + PumpSwap program
     in same tx = PumpSwap graduation (the common path since Mar 2025)
  2. pump.fun migration program logsSubscribe — every tx = Raydium graduation
     (older path, still active for some tokens)

Safety (no DexScreener data available at buy time):
  - Jupiter price impact < 10% (thin pool / not routable = skip)
  - Duplicate mint guard (5-min TTL)
  - Retries Jupiter up to 5× (pool may not be routable for first 1-2s)
  - PositionManager 20-min MC expiry + -25% stop as backstop
"""

import asyncio
import json
import logging
import os
import time
import aiohttp
from typing import Optional, Set
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Program addresses ─────────────────────────────────────────────────────────
PUMP_FUN_PROGRAM    = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_MIGRATION_PROG = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"  # pump → Raydium
PUMPSWAP_PROGRAM    = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"   # PumpSwap AMM
SOL_MINT            = "So11111111111111111111111111111111111111112"

# pump.fun log line emitted when bonding curve completes and funds withdraw to AMM
GRADUATE_LOG        = "Instruction: Withdraw"

# Known non-mint accounts to skip when scanning accountKeys as fallback
_KNOWN_PROGRAMS = {
    PUMP_FUN_PROGRAM, PUMP_MIGRATION_PROG, PUMPSWAP_PROGRAM, SOL_MINT,
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # SPL Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bea",  # Associated Token Account
    "11111111111111111111111111111111",                 # System program
    "ComputeBudget111111111111111111111111111111",      # Compute budget
    "SysvarRent111111111111111111111111111111111",
    "SysvarC1ock11111111111111111111111111111111",
}

# Jupiter quote API (matches existing trader.py logic)
_JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY", "")
if _JUPITER_API_KEY:
    _JUP_QUOTE = "https://api.jup.ag/swap/v1/quote"
    _JUP_HEADERS = {"x-api-key": _JUPITER_API_KEY}
else:
    _JUP_QUOTE = "https://quote-api.jup.ag/v6/quote"
    _JUP_HEADERS = {}


class GraduationSniper:
    """
    Real-time pump.fun graduation detector and buyer.
    Wires into trader.buy() with micro-cap position sizing.
    """

    # Public Solana RPC — all calls go here (no paid RPC needed).
    _PUBLIC_RPC_WS  = "wss://api.mainnet-beta.solana.com"
    _PUBLIC_RPC_HTTP = "https://api.mainnet-beta.solana.com"

    def __init__(self,
                 rpc_url: str,
                 trader,
                 position_usd: float = 40.0,
                 max_price_impact_pct: float = 10.0,
                 sol_price_usd: float = 150.0):
        # rpc_url param kept for API compatibility but ignored — all calls use public RPC
        self.rpc_url = self._PUBLIC_RPC_HTTP
        self.trader  = trader
        self.position_usd        = position_usd
        self.max_price_impact    = max_price_impact_pct
        self.sol_price_usd       = sol_price_usd

        self.ws_url = self._PUBLIC_RPC_WS

        # Dedup: mint → unix timestamp of first detection
        self._seen_mints: dict = {}
        self._seen_sigs:  Set[str] = set()
        self._SEEN_TTL = 300  # 5 minutes

        # Stats
        self.graduations_detected  = 0
        self.buys_attempted        = 0
        self.buys_skipped_impact   = 0
        self.buys_skipped_noroute  = 0
        self.buys_skipped_dup      = 0
        self._running = False
        self._msg_count = 0        # total WS messages received
        self._last_heartbeat = 0.0 # monotonic time of last heartbeat log
        self._last_buy_time = 0.0  # monotonic time of last successful buy
        self._BUY_COOLDOWN = 300   # 5 minutes between graduation buys

    # ── Public interface ──────────────────────────────────────────────────────

    async def run(self):
        """Main loop — two concurrent WebSocket subscriptions."""
        self._running = True
        logger.info("[GraduationSniper] Starting — subscribing to pump.fun graduation events")
        await asyncio.gather(
            self._ws_loop(),
        )

    async def _ws_loop(self):
        """WebSocket loop with exponential backoff reconnect."""
        backoff = 5
        while self._running:
            try:
                await self._connect_and_listen()
                backoff = 5
            except asyncio.CancelledError:
                logger.info("[GraduationSniper] Cancelled")
                break
            except Exception as e:
                logger.warning(
                    f"[GraduationSniper] WS connection lost: {e} — "
                    f"reconnecting in {backoff}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    def get_stats(self) -> dict:
        return {
            "graduations_detected": self.graduations_detected,
            "buys_attempted":       self.buys_attempted,
            "skipped_no_route":     self.buys_skipped_noroute,
            "skipped_impact":       self.buys_skipped_impact,
            "skipped_dup":          self.buys_skipped_dup,
        }

    # ── WebSocket connection ──────────────────────────────────────────────────

    async def _connect_and_listen(self):
        """
        Single WebSocket connection with two logsSubscribe subscriptions:

        Sub 1 — pump.fun program (id=1):
          HIGH traffic — every buy/sell/grad on pump.fun.
          We filter client-side: GRADUATE_LOG + PUMPSWAP_PROGRAM in logs → PumpSwap grad.

        Sub 2 — pump.fun migration program (id=2):
          LOW traffic — every tx is exactly one Raydium graduation. No filtering needed.

        Using public Solana RPC (no API key). High-traffic subs may cause the public
        RPC to disconnect occasionally; the _ws_loop reconnect handles that.
        """
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self.ws_url,
                heartbeat=20,
                timeout=aiohttp.ClientWSTimeout(ws_close=30),
            ) as ws:
                logger.info(f"[GraduationSniper] Connected — {self.ws_url}")

                # Sub 1: pump.fun program — catch PumpSwap graduations
                await ws.send_str(json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [PUMP_FUN_PROGRAM]},
                        {"commitment": "confirmed"},
                    ],
                }))

                # Sub 2: migration program — catch Raydium graduations
                await ws.send_str(json.dumps({
                    "jsonrpc": "2.0", "id": 2,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [PUMP_MIGRATION_PROG]},
                        {"commitment": "confirmed"},
                    ],
                }))

                logger.info(
                    "[GraduationSniper] ✅ Subscribed to pump.fun (PumpSwap grads) "
                    "and migration program (Raydium grads)"
                )

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._on_message(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break

    # ── Message handler ───────────────────────────────────────────────────────

    async def _on_message(self, raw: str):
        try:
            self._msg_count += 1
            now = time.monotonic()
            if now - self._last_heartbeat >= 300:  # log every 5 minutes
                logger.info(
                    f"[GraduationSniper] ♥ Alive — {self._msg_count} msgs received, "
                    f"{self.graduations_detected} grads detected"
                )
                self._last_heartbeat = now

            data = json.loads(raw)

            # Subscription confirmation — ignore
            if "result" in data:
                return

            value = (data.get("params") or {}).get("result", {}).get("value", {})
            sig   = value.get("signature", "")
            err   = value.get("err")
            logs  = value.get("logs") or []

            if err or not sig or not logs:
                return

            if sig in self._seen_sigs:
                return
            self._seen_sigs.add(sig)
            if len(self._seen_sigs) > 100_000:
                self._seen_sigs = set(list(self._seen_sigs)[-50_000:])

            logs_str = " ".join(logs)

            # Path 1: PumpSwap graduation
            # pump.fun emits "Instruction: Withdraw" when bonding curve completes.
            # PumpSwap program appears in the same tx (creates the AMM pool).
            is_pumpswap_grad = (
                GRADUATE_LOG        in logs_str and
                PUMP_FUN_PROGRAM    in logs_str and
                PUMPSWAP_PROGRAM    in logs_str
            )

            # Path 2: Raydium graduation
            # Every tx on the migration program is a graduation — no further filtering needed.
            is_raydium_grad = PUMP_MIGRATION_PROG in logs_str

            if not (is_pumpswap_grad or is_raydium_grad):
                return

            grad_type = "PumpSwap" if is_pumpswap_grad else "Raydium"
            self.graduations_detected += 1
            logger.info(
                f"[GraduationSniper] 🎓 GRADUATION detected ({grad_type}) | "
                f"tx: {sig[:20]}…"
            )

            # Handle async — don't block the WS reader
            asyncio.ensure_future(self._handle_graduation(sig, grad_type))

        except Exception as e:
            logger.debug(f"[GraduationSniper] Message parse error: {e}")

    # ── Graduation handler ────────────────────────────────────────────────────

    async def handle_axiom_graduation(self, mint: str, symbol: str):
        """
        Entry point called directly from the Axiom WS feed.
        Mint is already known — skips the RPC tx fetch step entirely.
        """
        self.graduations_detected += 1
        logger.info(
            f"[GraduationSniper] 🎓 GRADUATION via Axiom | "
            f"{symbol} | mint: {mint[:20]}…"
        )
        asyncio.ensure_future(self._execute_grad_buy(mint, symbol, "PumpSwap/Axiom"))

    async def _handle_graduation(self, sig: str, grad_type: str):
        try:
            # 1. Extract token mint from the transaction
            mint = await self._extract_mint(sig)
            if not mint:
                logger.debug(f"[GraduationSniper] Could not extract mint from {sig[:16]}…")
                return
            symbol = f"GRAD-{mint[:6]}"
            await self._execute_grad_buy(mint, symbol, grad_type)
        except Exception as e:
            logger.error(f"[GraduationSniper] handle_graduation error: {e}")

    async def _execute_grad_buy(self, mint: str, symbol: str, grad_type: str):
        try:
            mint_lower = mint.lower()

            # 1. Dedup guard
            now = time.time()
            if mint_lower in self._seen_mints:
                if now - self._seen_mints[mint_lower] < self._SEEN_TTL:
                    self.buys_skipped_dup += 1
                    logger.debug(f"[GraduationSniper] Dup mint: {mint[:12]}…")
                    return
            self._seen_mints[mint_lower] = now
            self._seen_mints = {k: v for k, v in self._seen_mints.items()
                                if now - v < self._SEEN_TTL}

            # 2. Already holding?
            if mint_lower in self.trader.open_positions:
                return

            # 2b. Rate limit — one graduation buy per 5 minutes max
            elapsed = time.monotonic() - self._last_buy_time
            if self._last_buy_time > 0 and elapsed < self._BUY_COOLDOWN:
                remaining = int(self._BUY_COOLDOWN - elapsed)
                logger.debug(
                    f"[GraduationSniper] Cooldown {remaining}s remaining — skip {mint[:12]}…"
                )
                return

            # 3. Jupiter quote — retry up to 5× (pool may not be routable for 1-2s post-grad)
            quote = None
            for attempt in range(5):
                quote = await self._jupiter_quote(mint)
                if quote is not None:
                    break
                if attempt < 4:
                    await asyncio.sleep(1.0)

            if quote is None:
                logger.info(
                    f"[GraduationSniper] Not routable after 5 attempts: {mint[:12]}… — skip"
                )
                self.buys_skipped_noroute += 1
                return

            # 4. Price impact check — high impact = very thin pool (likely rug or fake)
            price_impact = abs(float(quote.get("priceImpactPct") or 0)) * 100
            if price_impact > self.max_price_impact:
                logger.info(
                    f"[GraduationSniper] High price impact: {mint[:12]}… "
                    f"{price_impact:.1f}% > {self.max_price_impact}% — skip"
                )
                self.buys_skipped_impact += 1
                return

            # 5. Buy
            self.buys_attempted += 1
            logger.info(
                f"[GraduationSniper] 🚀 BUY {symbol} | "
                f"mint: {mint[:16]}… | "
                f"impact: {price_impact:.2f}% | "
                f"type: {grad_type}"
            )

            self._last_buy_time = time.monotonic()
            await self.trader.buy(
                token_address=mint,
                token_symbol=symbol,
                reason=f"Graduation sniper ({grad_type}) — micro-cap entry",
                signal_score=55,
                override_usd=self.position_usd,
                chain_id="solana",
                strategy="graduation",
                override_impact_pct=price_impact,
            )

        except Exception as e:
            logger.error(f"[GraduationSniper] _execute_grad_buy error: {e}")

    # ── RPC helpers ───────────────────────────────────────────────────────────

    async def _extract_mint(self, sig: str) -> Optional[str]:
        """
        Fetch the confirmed transaction and extract the non-SOL token mint.
        Primary: postTokenBalances (most reliable).
        Fallback: scan accountKeys for a plausible mint address.
        Uses public Solana RPC (no API key required).
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._PUBLIC_RPC_HTTP,
                    json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getTransaction",
                        "params": [sig, {
                            "encoding": "json",
                            "maxSupportedTransactionVersion": 0,
                            "commitment": "confirmed",
                        }],
                    },
                    timeout=aiohttp.ClientTimeout(total=6),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            tx = (data.get("result") or {})
            if not tx:
                return None

            meta = tx.get("meta") or {}

            # Primary: postTokenBalances — non-SOL mint is the graduated token
            for bal in meta.get("postTokenBalances") or []:
                mint = bal.get("mint", "")
                if mint and mint != SOL_MINT:
                    return mint

            # Fallback: scan accountKeys
            msg = (tx.get("transaction") or {}).get("message") or {}
            for key in msg.get("accountKeys") or []:
                addr = key if isinstance(key, str) else (key.get("pubkey") or "")
                if (addr
                        and len(addr) >= 32
                        and addr not in _KNOWN_PROGRAMS
                        and not addr.startswith("Sysvars")):
                    return addr

            return None

        except Exception as e:
            logger.debug(f"[GraduationSniper] _extract_mint error: {e}")
            return None

    async def _jupiter_quote(self, mint: str) -> Optional[dict]:
        """
        Get Jupiter routing quote for SOL → token.
        Returns quote dict if routable, None if not yet indexed or error.
        """
        try:
            # Refresh SOL price from trader's oracle on every quote — cheap
            # and avoids stale-price position-sizing errors as SOL moves.
            try:
                live_sol = await self.trader._get_token_price(SOL_MINT)
                if live_sol and live_sol > 0:
                    self.sol_price_usd = float(live_sol)
            except Exception:
                pass  # keep previous value
            lamports = int((self.position_usd / self.sol_price_usd) * 1e9)
            if lamports <= 0:
                return None

            params = {
                "inputMint":  SOL_MINT,
                "outputMint": mint,
                "amount":     str(lamports),
                "slippageBps": "2000",   # 20% — fresh pools are very volatile
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _JUP_QUOTE,
                    params=params,
                    headers=_JUP_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except Exception:
            return None
