"""
Solana RPC Price Feed — direct AMM price reading, no aggregator lag.

Sources by pool type:
  - Pump.fun bonding curve (pre-graduation):  Solana RPC getAccountInfo
  - All graduated tokens (PumpSwap, Raydium,  Jupiter Price API v2
    Meteora, Orca, LaunchLab, etc.):           (reads AMM state directly)

Polls every 0.5s. Provides AxiomPriceFeed-compatible interface:
  price_cache, price_timestamps, subscribe_token(), unsubscribe_token(), run()

Latency:
  Pump.fun bonding curve:  ~50-150ms  (one RPC call, batched)
  Graduated tokens:        ~300-600ms (Jupiter API)
  vs DexScreener:          5-15s      (aggregator delay)
"""

import asyncio
import base64
import logging
import struct
import time
from typing import Dict, Optional, Set

import aiohttp

logger = logging.getLogger(__name__)

# ── Pump.fun constants ──────────────────────────────────────────────────────
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_PUMP_PROTOCOLS  = {"pump amm", "pump", "pump.fun", "pumpfun"}

# Bonding curve Anchor account layout (after 8-byte discriminator):
#   virtual_token_reserves  u64  offset  8
#   virtual_sol_reserves    u64  offset 16
#   real_token_reserves     u64  offset 24
#   real_sol_reserves       u64  offset 32
#   token_total_supply      u64  offset 40
#   complete                bool offset 48
_BC_VTOKEN_OFFSET   = 8
_BC_VSOL_OFFSET     = 16
_BC_COMPLETE_OFFSET = 48
_BC_MIN_LEN         = 49

# Price: (virtual_sol [lamports] / virtual_token [raw]) / 1000 * sol_usd
# Factor 1000 = 1e9 (lamports→SOL) / 1e6 (raw→token)
_BC_PRICE_DIVISOR = 1_000

# Public Solana RPC (free, no key required)
SOLANA_RPC_URL   = "https://api.mainnet-beta.solana.com"
JUPITER_PRICE_V2 = "https://api.jup.ag/price/v2"
COINGECKO_SOL    = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"

# Polling interval — fast enough to be near-real-time
_POLL_INTERVAL  = 0.5   # seconds between price polls
_SOL_REFRESH    = 30    # seconds between SOL/USD refreshes
_JUPITER_BATCH  = 50    # max tokens per Jupiter request


class SolanaRpcPriceFeed:
    """
    Fast price feed using direct Solana RPC (pump.fun) and Jupiter API
    (all other pool types). Fills the gap when Axiom WS isn't available.

    Exposes the same interface as AxiomPriceFeed so position_manager and
    the dashboard can use it as a drop-in price source.
    """

    def __init__(self, rpc_url: str = SOLANA_RPC_URL):
        self.rpc_url = rpc_url

        # AxiomPriceFeed-compatible caches (keyed by lowercase token address)
        self.price_cache:      Dict[str, float] = {}
        self.price_timestamps: Dict[str, float] = {}
        self.volume_cache:     Dict[str, float] = {}
        self.liquidity_cache:  Dict[str, float] = {}

        # lowercase_addr → pool_type ("pump" | "other")
        self._watched:     Dict[str, str] = {}
        # lowercase_addr → original-case address (Solana/Jupiter APIs are case-sensitive)
        self._orig_addrs:  Dict[str, str] = {}
        # original_addr → bonding-curve PDA address (cached after first derivation)
        self._bc_addrs:    Dict[str, str] = {}
        # tokens where bonding curve was found complete / absent → use Jupiter
        self._graduated:   Set[str] = set()

        # SOL/USD (refreshed every 30s)
        self._sol_usd: float = 0.0

        # position_manager ref — fires realtime stop-loss on each price tick
        self.position_manager = None

        self._running   = False
        self._updates   = 0

    # ── Public API (AxiomPriceFeed-compatible) ──────────────────────────────

    def subscribe_token(self, token_address: str, pool_type: str = ""):
        addr = token_address.lower()
        if addr in self._watched:
            return
        is_pump = pool_type.lower() in _PUMP_PROTOCOLS
        self._watched[addr]    = "pump" if is_pump else "other"
        self._orig_addrs[addr] = token_address  # preserve original case for API calls
        logger.debug(
            f"[RpcPriceFeed] Subscribed {addr[:8]}… "
            f"(type={'pump' if is_pump else 'jupiter'})"
        )

    def unsubscribe_token(self, token_address: str):
        addr = token_address.lower()
        orig = self._orig_addrs.pop(addr, token_address)  # get before removing
        self._watched.pop(addr, None)
        self._graduated.discard(addr)
        self._bc_addrs.pop(orig, None)
        self.price_cache.pop(addr, None)
        self.price_timestamps.pop(addr, None)

    async def run(self):
        """Main loop — polls all watched tokens every 0.5s."""
        self._running = True
        logger.info("[RpcPriceFeed] Starting (Solana RPC + Jupiter Price API)")
        await asyncio.gather(
            self._poll_loop(),
            self._refresh_sol_price(),
        )

    def get_stats(self) -> dict:
        return {
            "watched":        len(self._watched),
            "price_updates":  self._updates,
            "sol_usd":        self._sol_usd,
            "pump_tokens":    sum(1 for t in self._watched.values() if t == "pump"),
            "jupiter_tokens": sum(1 for t in self._watched.values() if t != "pump"),
        }

    # ── SOL price refresh ───────────────────────────────────────────────────

    async def _refresh_sol_price(self):
        while self._running:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        COINGECKO_SOL, timeout=aiohttp.ClientTimeout(total=5)
                    ) as r:
                        if r.status == 200:
                            d = await r.json()
                            p = float((d.get("solana") or {}).get("usd") or 0)
                            if p > 0:
                                self._sol_usd = p
            except Exception:
                pass
            await asyncio.sleep(_SOL_REFRESH)

    # ── Main poll loop ──────────────────────────────────────────────────────

    async def _poll_loop(self):
        while self._running:
            if self._watched and self._sol_usd > 0:
                await self._poll_all()
            elif self._watched and self._sol_usd == 0:
                # No SOL price yet → use Jupiter only (returns USD directly)
                all_addrs = list(self._watched.keys())
                await self._fetch_jupiter(all_addrs)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _poll_all(self):
        """Split watched tokens by type and fetch prices concurrently."""
        pump_tokens  = [a for a, t in self._watched.items()
                        if t == "pump" and a not in self._graduated]
        other_tokens = [a for a, t in self._watched.items()
                        if t != "pump" or a in self._graduated]

        tasks = []
        if pump_tokens:
            tasks.append(self._fetch_pump(pump_tokens))
        if other_tokens:
            tasks.append(self._fetch_jupiter(other_tokens))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── Pump.fun bonding curve reader ───────────────────────────────────────

    async def _fetch_pump(self, token_addrs: list):
        """Batch-read pump.fun bonding curve accounts via getMultipleAccounts."""
        bc_map: Dict[str, str] = {}  # lowercase_addr → bc_addr
        for addr in token_addrs:
            # PDA derivation requires original-case address (Solana is case-sensitive)
            orig = self._orig_addrs.get(addr, addr)
            bc = await self._get_bc_address(orig)
            if bc:
                bc_map[addr] = bc

        if not bc_map:
            await self._fetch_jupiter(token_addrs)
            return

        # One RPC call for all bonding curves
        bc_addresses = list(bc_map.values())
        token_for_bc = {v: k for k, v in bc_map.items()}

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getMultipleAccounts",
                "params": [
                    bc_addresses,
                    {"encoding": "base64", "commitment": "processed"}
                ]
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=4)
                ) as r:
                    if r.status != 200:
                        await self._fetch_jupiter(token_addrs)
                        return
                    data = await r.json()
                    accounts = (data.get("result") or {}).get("value") or []

            graduated_now = []
            for i, account in enumerate(accounts):
                if i >= len(bc_addresses):
                    break
                bc_addr    = bc_addresses[i]
                token_addr = token_for_bc.get(bc_addr)
                if not token_addr:
                    continue

                if account is None:
                    # Bonding curve account doesn't exist → token graduated
                    self._graduated.add(token_addr)
                    graduated_now.append(token_addr)
                    continue

                raw_b64 = (account.get("data") or [None])[0]
                if not raw_b64:
                    continue
                raw = base64.b64decode(raw_b64)
                if len(raw) < _BC_MIN_LEN:
                    continue

                complete = raw[_BC_COMPLETE_OFFSET]
                if complete:
                    self._graduated.add(token_addr)
                    graduated_now.append(token_addr)
                    continue

                vtoken = struct.unpack_from("<Q", raw, _BC_VTOKEN_OFFSET)[0]
                vsol   = struct.unpack_from("<Q", raw, _BC_VSOL_OFFSET)[0]
                if vtoken <= 0:
                    continue

                price_usd = (vsol / vtoken / _BC_PRICE_DIVISOR) * self._sol_usd
                if price_usd > 0:
                    self._emit(token_addr, price_usd)

            # Graduated tokens: fetch from Jupiter immediately
            if graduated_now:
                await self._fetch_jupiter(graduated_now)

        except Exception as e:
            logger.debug(f"[RpcPriceFeed] getMultipleAccounts error: {e}")
            await self._fetch_jupiter(token_addrs)

    async def _get_bc_address(self, token_address: str) -> Optional[str]:
        """Derive the pump.fun bonding curve PDA for a token mint (cached)."""
        if token_address in self._bc_addrs:
            return self._bc_addrs[token_address]
        try:
            from solders.pubkey import Pubkey
            mint    = Pubkey.from_string(token_address)
            program = Pubkey.from_string(PUMP_PROGRAM_ID)
            bc, _   = Pubkey.find_program_address(
                [b"bonding-curve", bytes(mint)], program
            )
            result = str(bc)
            self._bc_addrs[token_address] = result
            return result
        except Exception as e:
            logger.debug(f"[RpcPriceFeed] PDA derivation failed for {token_address[:8]}: {e}")
            return None

    # ── Jupiter Price API reader ─────────────────────────────────────────────

    async def _fetch_jupiter(self, token_addrs: list):
        """Fetch prices from Jupiter Price API v2 (covers all pool types)."""
        for i in range(0, len(token_addrs), _JUPITER_BATCH):
            batch = token_addrs[i:i + _JUPITER_BATCH]
            await self._fetch_jupiter_batch(batch)

    async def _fetch_jupiter_batch(self, addrs: list):
        # addrs are lowercase — Jupiter API is case-sensitive, use original-case in URL
        orig_addrs    = [self._orig_addrs.get(a, a) for a in addrs]
        orig_to_lower = {self._orig_addrs.get(a, a): a for a in addrs}
        try:
            url = f"{JUPITER_PRICE_V2}?ids={','.join(orig_addrs)}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=4)) as r:
                    if r.status != 200:
                        return
                    data = await r.json()
                    prices = data.get("data") or {}
                    for orig in orig_addrs:
                        item = prices.get(orig) or prices.get(orig.lower())
                        if item:
                            price = float(item.get("price") or 0)
                            if price > 0:
                                lower = orig_to_lower.get(orig, orig.lower())
                                self._emit(lower, price)
        except Exception as e:
            logger.debug(f"[RpcPriceFeed] Jupiter batch error: {e}")

    # ── Cache update ─────────────────────────────────────────────────────────

    def _emit(self, token_address: str, price_usd: float):
        """Store price in cache and fire realtime stop-loss check."""
        self.price_cache[token_address]      = price_usd
        self.price_timestamps[token_address] = time.time()
        self._updates += 1

        if self.position_manager is not None:
            self.position_manager.check_stop_loss_realtime(token_address, price_usd)
            self.position_manager.check_take_profit_realtime(token_address, price_usd)
            self.position_manager.check_exhaustion_realtime(token_address, price_usd)
            self.position_manager.check_post_tp1_trail_realtime(token_address, price_usd)
