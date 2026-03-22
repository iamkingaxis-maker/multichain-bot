"""
On-Chain Solana Program Monitor
Subscribes directly to Solana program logs via Helius WebSocket.
Detects token creation, liquidity addition, and large trades
at the blockchain level — before DexScreener or any aggregator.

Latency: ~5-20ms vs ~2-10 seconds with polling.

Programs monitored:
  - Pump.fun program       — new token launches
  - Raydium AMM            — liquidity pool creation & trades
  - Raydium CLMM           — concentrated liquidity events
  - Orca Whirlpool         — Orca DEX events
  - Metaplex Token Metadata — token metadata creation
"""

import asyncio
import json
import logging
import aiohttp
from typing import Callable, Dict, List, Optional, Set
from datetime import datetime, timezone
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Solana program addresses
PROGRAMS = {
    "pump_fun":          "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "raydium_amm":       "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "raydium_clmm":      "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    "orca_whirlpool":    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "token_metadata":    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
    "token_program":     "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "system_program":    "11111111111111111111111111111111"
}

# Pump.fun specific instruction discriminators
PUMP_FUN_CREATE    = "Program log: Instruction: Create"
PUMP_FUN_BUY      = "Program log: Instruction: Buy"
PUMP_FUN_SELL     = "Program log: Instruction: Sell"
RAYDIUM_INIT_POOL = "Program log: Instruction: Initialize"
RAYDIUM_ADD_LIQ   = "Program log: Instruction: AddLiquidity"


@dataclass
class OnChainEvent:
    """A raw on-chain event detected from program logs."""
    event_type: str          # "new_token", "liquidity_added", "large_buy", "large_sell"
    program: str             # Which program emitted this
    signature: str           # Transaction signature
    token_address: str       # Token mint address (if identifiable)
    token_symbol: str        # Token symbol (if available)
    amount_sol: float        # SOL amount involved
    timestamp: datetime
    raw_logs: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


class SolanaProgramMonitor:
    """
    Monitors Solana programs in real-time via Helius WebSocket.
    Fires callbacks when significant events are detected.
    Much faster than polling DexScreener for new tokens.
    """

    def __init__(self,
                 helius_api_key: str,
                 on_new_token: Optional[Callable] = None,
                 on_liquidity_added: Optional[Callable] = None,
                 on_large_buy: Optional[Callable] = None,
                 large_buy_threshold_sol: float = 5.0):
        self.api_key = helius_api_key
        self.on_new_token = on_new_token
        self.on_liquidity_added = on_liquidity_added
        self.on_large_buy = on_large_buy
        self.large_buy_threshold = large_buy_threshold_sol

        self.ws_url = f"wss://mainnet.helius-rpc.com/?api-key={helius_api_key}"
        self.http_url = f"https://mainnet.helius-rpc.com/?api-key={helius_api_key}"

        self._subscription_ids: Dict[str, int] = {}
        self._seen_signatures: Set[str] = set()
        self._event_count = 0
        self._running = False

        # Stats
        self.new_tokens_detected = 0
        self.liquidity_events = 0
        self.large_buys_detected = 0

    async def run(self):
        """Main monitoring loop with auto-reconnect."""
        self._running = True
        logger.info("[SolanaProgramMonitor] Starting on-chain monitoring...")

        while self._running:
            try:
                await self._connect_and_monitor()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[SolanaProgramMonitor] Connection error: {e}")

            logger.info("[SolanaProgramMonitor] Reconnecting in 5s...")
            await asyncio.sleep(5)

    async def _connect_and_monitor(self):
        """Connect to Helius WebSocket and subscribe to programs."""
        logger.info("[SolanaProgramMonitor] Connecting to Helius WebSocket...")

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self.ws_url,
                heartbeat=30,
                timeout=aiohttp.ClientWSTimeout(ws_close=60)
            ) as ws:
                logger.info("[SolanaProgramMonitor] Connected — subscribing to programs")

                # Subscribe to key programs
                await self._subscribe_to_program(ws, PROGRAMS["pump_fun"], "pump_fun")
                await self._subscribe_to_program(ws, PROGRAMS["raydium_amm"], "raydium_amm")
                await self._subscribe_to_program(ws, PROGRAMS["raydium_clmm"], "raydium_clmm")
                await self._subscribe_to_program(ws, PROGRAMS["orca_whirlpool"], "orca")

                logger.info("[SolanaProgramMonitor] Subscribed — listening for events")

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.ERROR,
                                      aiohttp.WSMsgType.CLOSED):
                        break

    async def _subscribe_to_program(self, ws, program_address: str,
                                     program_name: str):
        """Subscribe to logs for a specific program."""
        sub_msg = {
            "jsonrpc": "2.0",
            "id": len(self._subscription_ids) + 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [program_address]},
                {"commitment": "confirmed"}
            ]
        }
        await ws.send_str(json.dumps(sub_msg))
        self._subscription_ids[program_name] = sub_msg["id"]
        logger.debug(f"[SolanaProgramMonitor] Subscribed to {program_name}")

    async def _handle_message(self, raw: str):
        """Parse incoming WebSocket message and fire appropriate callbacks."""
        try:
            data = json.loads(raw)

            # Subscription confirmation
            if "result" in data and isinstance(data["result"], int):
                return

            # Log notification
            params = data.get("params", {})
            result = params.get("result", {})
            value = result.get("value", {})

            logs = value.get("logs", [])
            signature = value.get("signature", "")
            err = value.get("err")

            # Skip failed transactions
            if err or not logs or not signature:
                return

            # Skip already-seen transactions
            if signature in self._seen_signatures:
                return
            self._seen_signatures.add(signature)

            # Keep seen set bounded
            if len(self._seen_signatures) > 10_000:
                self._seen_signatures = set(list(self._seen_signatures)[-5_000:])

            self._event_count += 1
            await self._classify_and_dispatch(logs, signature)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug(f"[SolanaProgramMonitor] Message parse error: {e}")

    async def _classify_and_dispatch(self, logs: List[str], signature: str):
        """Classify the event type and call appropriate handler."""
        logs_text = " ".join(logs)

        # Detect Pump.fun new token creation
        if PROGRAMS["pump_fun"] in logs_text and "Create" in logs_text:
            event = await self._build_pump_fun_create_event(logs, signature)
            if event and self.on_new_token:
                self.new_tokens_detected += 1
                logger.info(
                    f"[SolanaProgramMonitor] 🆕 New Pump.fun token: "
                    f"{event.token_address[:12]}... | tx: {signature[:12]}..."
                )
                if asyncio.iscoroutinefunction(self.on_new_token):
                    await self.on_new_token(event)
                else:
                    self.on_new_token(event)

        # Detect Raydium pool initialization (token graduating from Pump.fun)
        elif PROGRAMS["raydium_amm"] in logs_text and "Initialize" in logs_text:
            event = await self._build_liquidity_event(logs, signature, "raydium_amm")
            if event and self.on_liquidity_added:
                self.liquidity_events += 1
                logger.info(
                    f"[SolanaProgramMonitor] 💧 Raydium pool created: "
                    f"{signature[:12]}..."
                )
                if asyncio.iscoroutinefunction(self.on_liquidity_added):
                    await self.on_liquidity_added(event)
                else:
                    self.on_liquidity_added(event)

        # Detect large buys
        elif "Buy" in logs_text or "Swap" in logs_text:
            event = await self._build_trade_event(logs, signature)
            if event and event.amount_sol >= self.large_buy_threshold:
                if self.on_large_buy:
                    self.large_buys_detected += 1
                    logger.info(
                        f"[SolanaProgramMonitor] 🐋 Large buy: "
                        f"{event.amount_sol:.1f} SOL | {signature[:12]}..."
                    )
                    if asyncio.iscoroutinefunction(self.on_large_buy):
                        await self.on_large_buy(event)
                    else:
                        self.on_large_buy(event)

    async def _build_pump_fun_create_event(self, logs: List[str],
                                            signature: str) -> Optional[OnChainEvent]:
        """Extract token info from a Pump.fun Create transaction."""
        try:
            # Fetch full transaction details to extract the mint address
            tx_data = await self._fetch_transaction(signature)
            if not tx_data:
                return None

            # Extract token mint from account keys
            account_keys = tx_data.get("result", {}).get(
                "transaction", {}
            ).get("message", {}).get("accountKeys", [])

            # The new mint is typically the second account key in Pump.fun creates
            token_address = ""
            for key in account_keys:
                addr = key if isinstance(key, str) else key.get("pubkey", "")
                if addr and addr != PROGRAMS["pump_fun"] and len(addr) > 30:
                    token_address = addr
                    break

            if not token_address:
                return None

            return OnChainEvent(
                event_type="new_token",
                program="pump_fun",
                signature=signature,
                token_address=token_address,
                token_symbol="NEW",
                amount_sol=0.0,
                timestamp=datetime.now(timezone.utc),
                raw_logs=logs,
                metadata={"source": "pump_fun_create"}
            )
        except Exception as e:
            logger.debug(f"[SolanaProgramMonitor] Pump.fun create parse error: {e}")
            return None

    async def _build_liquidity_event(self, logs: List[str],
                                      signature: str,
                                      program: str) -> Optional[OnChainEvent]:
        """Build a liquidity addition event."""
        return OnChainEvent(
            event_type="liquidity_added",
            program=program,
            signature=signature,
            token_address="",
            token_symbol="",
            amount_sol=0.0,
            timestamp=datetime.now(timezone.utc),
            raw_logs=logs,
            metadata={"source": program}
        )

    async def _build_trade_event(self, logs: List[str],
                                  signature: str) -> Optional[OnChainEvent]:
        """Build a trade event, extracting SOL amount if possible."""
        try:
            # Try to extract SOL amount from logs
            sol_amount = 0.0
            for log in logs:
                if "sol_amount" in log.lower() or "amount" in log.lower():
                    parts = log.split(":")
                    if len(parts) > 1:
                        try:
                            amount_str = parts[-1].strip().split()[0]
                            raw_amount = float(amount_str)
                            # Convert lamports to SOL if needed
                            if raw_amount > 1_000_000:
                                raw_amount /= 1e9
                            sol_amount = raw_amount
                        except (ValueError, IndexError):
                            pass

            return OnChainEvent(
                event_type="large_buy",
                program="unknown",
                signature=signature,
                token_address="",
                token_symbol="",
                amount_sol=sol_amount,
                timestamp=datetime.now(timezone.utc),
                raw_logs=logs
            )
        except Exception:
            return None

    async def _fetch_transaction(self, signature: str) -> Optional[dict]:
        """Fetch full transaction details from RPC."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.http_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except Exception as e:
            logger.debug(f"[SolanaProgramMonitor] TX fetch error: {e}")
            return None

    def get_stats(self) -> dict:
        return {
            "events_processed": self._event_count,
            "new_tokens": self.new_tokens_detected,
            "liquidity_events": self.liquidity_events,
            "large_buys": self.large_buys_detected,
            "seen_signatures": len(self._seen_signatures)
        }
