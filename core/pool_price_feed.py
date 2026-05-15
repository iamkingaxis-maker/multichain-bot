"""
Solana Pool Price Feed — True On-Chain Real-Time Prices

PROBLEM SOLVED:
  DexScreener's REST API caches prices for 15-20 seconds on their end.
  Even with WebSocket triggering immediate re-fetches, we get the stale
  DexScreener price for up to 20 seconds.

SOLUTION:
  Subscribe to the pool's vault accounts via Helius WebSocket.
  Each accountNotification already contains the new account data —
  we decode SPL token balances directly and compute price from reserves.
  No DexScreener needed. No additional API call. Sub-second latency.

Supported pool types:
  - Raydium AMM v4: fetch vault addresses → subscribe to both vaults
      → decode SPL balance (u64 at offset 64) → price = sol_amount / token_amount
  - Pump.fun bonding curve: subscribe to pair account
      → decode virtual reserves → price = vs / vt (adjusted for decimals)
  - Unknown DEX: trigger immediate DexScreener re-fetch on pool change
      (better than 8s polling, even though DexScreener is slower than on-chain)

Latency:
  Raydium: ~100-400ms (one Solana slot) — on-chain data, no API call
  Pump.fun: ~100-400ms (decoded from bonding curve account)
  Unknown:  ~1-5s (DexScreener triggered immediately on swap)

IMPORTANT: Solana addresses are case-sensitive base58. We store originals
for WebSocket subscriptions and lowercase for dict key lookups.
"""

import asyncio
import base64
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Set

import aiohttp

logger = logging.getLogger(__name__)

WSOL_MINT        = "So11111111111111111111111111111111111111112"
RAYDIUM_POOLS_API = "https://api-v3.raydium.io/pools/info/ids?ids="
COINGECKO_SOL    = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"

_DEBOUNCE_SECS = 0.3   # minimum gap between price writes for same token
_SANITY_MAX_JUMP = 15.0  # reject if new price is >15× the last known price


@dataclass
class PoolEntry:
    """State for one tracked position's pool."""
    token_address_lower: str    # lowercase key
    pair_address: str           # ORIGINAL case — used for WebSocket sub
    pool_type: str              # "raydium" | "pump" | "unknown"

    # Raydium vault accounts (ORIGINAL case for subscriptions)
    base_vault: str = ""        # holds the memecoin
    quote_vault: str = ""       # holds SOL (WSOL)
    base_decimals: int = 6
    quote_decimals: int = 9     # SOL default
    is_sol_quote: bool = True

    # Current reserve amounts (updated from each notification)
    base_amount: float = 0.0    # raw units (before decimal adjustment)
    quote_amount: float = 0.0

    # Fallback debounce
    last_fallback_ts: float = 0.0


class PoolPriceFeed:
    """
    Helius WebSocket feed that computes prices directly from on-chain reserves.
    Writes prices into the PositionManager's _dex_volume_cache so _update_price
    picks them up on the next 1s management cycle tick.
    """

    def __init__(
        self,
        helius_api_key: str,
        price_cache: Optional[Dict] = None,        # legacy shared-cache hook, optional
        fallback_fetch: Optional[Callable] = None, # legacy fallback hook, optional
    ):
        self.api_key      = helius_api_key
        self.ws_url       = f"wss://mainnet.helius-rpc.com/?api-key={helius_api_key}"
        # Local cache when no shared dict is provided. position_manager attribute
        # below is the primary integration path — direct realtime calls on each
        # decoded tick (matches SolanaRpcPriceFeed contract).
        self._price_cache: Dict = price_cache if price_cache is not None else {}
        self._fallback    = fallback_fetch

        # AxiomPriceFeed-compatible interface so trader.py can use this as a
        # drop-in feed alongside the existing dex/rpc feeds. position_manager
        # is set by the caller; when set, decoded prices fire
        # check_stop_loss_realtime + check_take_profit_realtime directly,
        # bypassing the DexScreener-indexer lag entirely.
        self.position_manager = None

        # Pool registry
        self._pools: Dict[str, PoolEntry] = {}           # lower_token → PoolEntry
        self._pair_lower_to_token: Dict[str, str] = {}   # lower_pair → lower_token
        self._vault_lower_to_token: Dict[str, str] = {}  # lower_vault → lower_token

        # Address case: lower → original (Solana is case-sensitive)
        self._orig: Dict[str, str] = {}   # lower_addr → original_addr

        # WebSocket subscription state
        self._sub_to_addr: Dict[int, str] = {}   # sub_id → lower_addr
        self._addr_to_sub: Dict[str, int] = {}   # lower_addr → sub_id
        self._pending: Dict[int, str] = {}        # req_id → lower_addr (awaiting confirm)

        # Pending ops (filled by register/unregister, flushed by _flush_pending)
        self._to_sub: Set[str] = set()    # lower addresses to subscribe
        self._to_unsub: Set[int] = set()  # sub_ids to unsubscribe

        # SOL price (refreshed in background)
        self._sol_usd: float = 150.0
        self._running = False
        self._req_id  = 1

        # Debounce
        self._last_write: Dict[str, float] = {}  # lower_token → monotonic ts

        # Stats
        self.on_chain_prices = 0
        self.fallback_triggers = 0

    # ───────────────────────── public API ─────────────────────────────────────

    # AxiomPriceFeed/SolanaRpcPriceFeed-compatible interface. trader.py calls
    # subscribe_token on every buy; we forward to register(). pair_address
    # is REQUIRED for pool decoding — silently no-op if missing (no pool to
    # subscribe to). chain_id and pool_type are accepted for interface parity
    # but ignored — we always operate on Solana pools.
    def subscribe_token(self, token_address: str, chain_id: str = "solana",
                          pair_address: str = "", dex_id: str = "",
                          pool_type: str = ""):
        if not pair_address:
            logger.debug(
                f"[PoolFeed] subscribe_token({token_address[:12]}…) skipped "
                f"— no pair_address provided"
            )
            return
        self.register(token_address, pair_address, dex_id=(dex_id or pool_type))

    def unsubscribe_token(self, token_address: str):
        self.unregister(token_address)

    def register(self, token_address: str, pair_address: str, dex_id: str = ""):
        """Register a pool to watch. Safe to call from any coroutine."""
        tl = token_address.lower()
        pl = pair_address.lower()

        if tl in self._pools:
            return  # already watching

        dtype = dex_id.lower() if dex_id else ""
        if "raydium" in dtype:
            ptype = "raydium"
        elif "pump" in dtype or "pump" in pl:
            ptype = "pump"
        else:
            ptype = "unknown"

        entry = PoolEntry(
            token_address_lower=tl,
            pair_address=pair_address,   # preserve original case
            pool_type=ptype,
        )
        self._pools[tl] = entry
        self._pair_lower_to_token[pl] = tl
        self._orig[pl] = pair_address

        logger.info(
            f"[PoolFeed] Registered pool {pair_address[:16]}… "
            f"type={ptype} token={token_address[:12]}…"
        )

        # Always try the Raydium API first — DexScreener's dex_id is unreliable.
        # Graduated pump-fun tokens that migrated to Raydium still show dex_id="pump-fun".
        # _lookup_raydium_vaults falls back to pair subscription if the API returns nothing.
        asyncio.create_task(self._lookup_raydium_vaults(entry))

    def unregister(self, token_address: str):
        """Stop watching all accounts for a token (call on position close)."""
        tl = token_address.lower()
        entry = self._pools.pop(tl, None)
        if not entry:
            return

        pl = entry.pair_address.lower()
        self._pair_lower_to_token.pop(pl, None)

        accounts_to_drop = [pl]
        if entry.base_vault:
            bl = entry.base_vault.lower()
            accounts_to_drop.append(bl)
            self._vault_lower_to_token.pop(bl, None)
        if entry.quote_vault:
            ql = entry.quote_vault.lower()
            accounts_to_drop.append(ql)
            self._vault_lower_to_token.pop(ql, None)

        for al in accounts_to_drop:
            sid = self._addr_to_sub.pop(al, None)
            if sid is not None:
                self._sub_to_addr.pop(sid, None)
                self._to_unsub.add(sid)
            self._orig.pop(al, None)

        logger.debug(f"[PoolFeed] Unregistered {token_address[:12]}…")

    def get_stats(self) -> dict:
        return {
            "pools_watched":     len(self._pools),
            "on_chain_prices":   self.on_chain_prices,
            "fallback_triggers": self.fallback_triggers,
            "sol_price_usd":     round(self._sol_usd, 2),
        }

    # ───────────────────────── vault lookup ───────────────────────────────────

    async def _lookup_raydium_vaults(self, entry: PoolEntry):
        """Fetch vault addresses from Raydium API, then subscribe to them."""
        pl = entry.pair_address.lower()
        try:
            url = f"{RAYDIUM_POOLS_API}{entry.pair_address}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status != 200:
                        logger.debug(
                            f"[PoolFeed] Raydium API {r.status} for {entry.pair_address[:16]}… "
                            f"— falling back to DexScreener trigger"
                        )
                        self._queue_sub(pl, entry.pair_address)
                        return
                    data = await r.json()

            pool_data = self._extract_pool_data(data)
            if not pool_data:
                logger.debug(f"[PoolFeed] No pool data from Raydium — falling back")
                self._queue_sub(pl, entry.pair_address)
                return

            vault   = pool_data.get("vault", {})
            mint_a  = pool_data.get("mintA", {})
            mint_b  = pool_data.get("mintB", {})
            vault_a = vault.get("A", "")
            vault_b = vault.get("B", "")
            addr_a  = mint_a.get("address", "")
            addr_b  = mint_b.get("address", "")

            if not vault_a or not vault_b:
                logger.debug(f"[PoolFeed] Missing vault addresses — falling back")
                self._queue_sub(pl, entry.pair_address)
                return

            # Determine which vault is WSOL (quote) and which is the token (base)
            if addr_a == WSOL_MINT:
                entry.quote_vault = vault_a     # A = WSOL = quote
                entry.base_vault  = vault_b     # B = token = base
                entry.base_decimals  = int(mint_b.get("decimals", 6))
                entry.quote_decimals = 9
            elif addr_b == WSOL_MINT:
                entry.quote_vault = vault_b     # B = WSOL = quote
                entry.base_vault  = vault_a     # A = token = base
                entry.base_decimals  = int(mint_a.get("decimals", 6))
                entry.quote_decimals = 9
            else:
                # No WSOL — USDC or other stablecoin quote; treat A=base, B=quote
                entry.base_vault  = vault_a
                entry.quote_vault = vault_b
                entry.base_decimals  = int(mint_a.get("decimals", 6))
                entry.quote_decimals = int(mint_b.get("decimals", 6))
                entry.is_sol_quote = False

            bvl = entry.base_vault.lower()
            qvl = entry.quote_vault.lower()
            self._vault_lower_to_token[bvl] = entry.token_address_lower
            self._vault_lower_to_token[qvl] = entry.token_address_lower

            # Vault lookup succeeded — this IS a Raydium pool regardless of dex_id label
            entry.pool_type = "raydium"

            self._queue_sub(bvl, entry.base_vault)
            self._queue_sub(qvl, entry.quote_vault)

            logger.info(
                f"[PoolFeed] Raydium vaults for {entry.token_address_lower[:12]}…: "
                f"base={entry.base_vault[:12]}… quote={entry.quote_vault[:12]}…"
            )

        except Exception as e:
            logger.debug(f"[PoolFeed] Vault lookup error: {e} — falling back")
            self._queue_sub(pl, entry.pair_address)

    @staticmethod
    def _extract_pool_data(data: dict) -> Optional[dict]:
        """Parse different Raydium API response formats."""
        raw = data.get("data", {})
        if isinstance(raw, list) and raw:
            return raw[0]
        if isinstance(raw, dict):
            inner = raw.get("data", [])
            if isinstance(inner, list) and inner:
                return inner[0]
            # Some versions return the pool directly
            if "vault" in raw:
                return raw
        return None

    def _queue_sub(self, addr_lower: str, addr_orig: str):
        """Queue an address for subscription on next WebSocket flush."""
        self._orig[addr_lower] = addr_orig
        self._to_sub.add(addr_lower)

    # ───────────────────────── main loop ──────────────────────────────────────

    async def run(self):
        self._running = True
        asyncio.create_task(self._sol_price_loop())
        logger.info("[PoolFeed] Starting Solana pool price feed (on-chain reserve decode)…")

        backoff = 5.0
        while self._running:
            try:
                await self._connect_and_watch()
                backoff = 5.0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[PoolFeed] Disconnected: {e} — reconnecting in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 60.0)

    async def _sol_price_loop(self):
        """Keep SOL/USD price fresh every 60s."""
        while self._running:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(COINGECKO_SOL, timeout=aiohttp.ClientTimeout(total=5)) as r:
                        if r.status == 200:
                            d = await r.json()
                            price = float((d.get("solana") or {}).get("usd") or 0)
                            if price > 0:
                                self._sol_usd = price
                                logger.debug(f"[PoolFeed] SOL price refreshed: ${price:.2f}")
            except Exception:
                pass
            await asyncio.sleep(60)

    async def _connect_and_watch(self):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self.ws_url,
                heartbeat=30,
                timeout=aiohttp.ClientWSTimeout(ws_close=60),
            ) as ws:
                logger.info("[PoolFeed] Connected to Helius WebSocket")

                # New connection — clear stale sub state
                self._sub_to_addr.clear()
                self._addr_to_sub.clear()
                self._pending.clear()

                # Re-queue every known account (vaults + pairs)
                for entry in self._pools.values():
                    pl = entry.pair_address.lower()
                    if entry.base_vault:
                        bvl = entry.base_vault.lower()
                        qvl = entry.quote_vault.lower()
                        self._queue_sub(bvl, entry.base_vault)
                        self._queue_sub(qvl, entry.quote_vault)
                    else:
                        self._queue_sub(pl, entry.pair_address)

                await self._flush_pending(ws)

                while True:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                    except asyncio.TimeoutError:
                        await self._flush_pending(ws)
                        continue

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(msg.data)
                        await self._flush_pending(ws)
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break

    async def _flush_pending(self, ws):
        """Send queued subscribe/unsubscribe messages."""
        for al in list(self._to_sub):
            self._to_sub.discard(al)
            orig = self._orig.get(al, al)   # use original case for Helius
            rid = self._req_id
            self._req_id += 1
            self._pending[rid] = al
            await ws.send_str(json.dumps({
                "jsonrpc": "2.0",
                "id":      rid,
                "method":  "accountSubscribe",
                "params":  [orig, {"encoding": "base64", "commitment": "processed"}],
            }))
            logger.debug(f"[PoolFeed] Subscribed to {orig[:16]}…")

        for sid in list(self._to_unsub):
            self._to_unsub.discard(sid)
            rid = self._req_id
            self._req_id += 1
            await ws.send_str(json.dumps({
                "jsonrpc": "2.0",
                "id":      rid,
                "method":  "accountUnsubscribe",
                "params":  [sid],
            }))

    # ───────────────────────── message parsing ────────────────────────────────

    async def _handle_message(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Subscription confirmation: {"id": N, "result": <int sub_id>}
        if "result" in data and "id" in data and isinstance(data.get("result"), int):
            rid    = data["id"]
            sub_id = data["result"]
            al     = self._pending.pop(rid, None)
            if al:
                self._addr_to_sub[al] = sub_id
                self._sub_to_addr[sub_id] = al
                logger.debug(f"[PoolFeed] Confirmed sub={sub_id} addr={al[:16]}…")
            return

        if data.get("method") != "accountNotification":
            return

        params = data.get("params", {})
        sub_id = params.get("subscription")
        if sub_id is None:
            return

        al = self._sub_to_addr.get(sub_id)
        if not al:
            return

        value    = params.get("result", {}).get("value", {})
        acct_data = value.get("data", [])
        if not acct_data or not isinstance(acct_data, list):
            return

        await self._dispatch(al, acct_data[0])

    async def _dispatch(self, addr_lower: str, data_b64: str):
        """Route notification to the correct decoder."""
        # Raydium vault?
        tl = self._vault_lower_to_token.get(addr_lower)
        if tl:
            await self._on_vault(addr_lower, tl, data_b64)
            return

        # Pump.fun / unknown pair account?
        tl = self._pair_lower_to_token.get(addr_lower)
        if tl:
            entry = self._pools.get(tl)
            if entry and entry.pool_type == "pump":
                await self._on_pump(tl, data_b64)
            else:
                await self._on_unknown(tl)

    # ───────────────────────── Raydium vault decode ───────────────────────────

    async def _on_vault(self, vault_lower: str, token_lower: str, data_b64: str):
        """Decode SPL token balance and recompute pool price."""
        entry = self._pools.get(token_lower)
        if not entry:
            return

        amount = _decode_spl_amount(data_b64)
        if amount is None:
            return

        if vault_lower == entry.base_vault.lower():
            entry.base_amount = amount
        else:
            entry.quote_amount = amount

        # Compute USD price if we have both reserves
        if entry.base_amount > 0 and entry.quote_amount > 0:
            base_real  = entry.base_amount  / (10 ** entry.base_decimals)
            quote_real = entry.quote_amount / (10 ** entry.quote_decimals)
            if base_real > 0:
                price_sol = quote_real / base_real
                price_usd = price_sol * self._sol_usd if entry.is_sol_quote else price_sol
                self._write_price(token_lower, price_usd, source="raydium-vault")

    # ───────────────────────── Pump.fun decode ────────────────────────────────

    async def _on_pump(self, token_lower: str, data_b64: str):
        """Decode Pump.fun bonding curve virtual reserves → price."""
        try:
            raw = base64.b64decode(data_b64)
            if len(raw) < 24:
                return
            # Layout: [0-7] discriminator, [8-15] virtual_token_reserves (u64),
            #         [16-23] virtual_sol_reserves (u64)
            vt = struct.unpack_from('<Q', raw, 8)[0]
            vs = struct.unpack_from('<Q', raw, 16)[0]
            if vt == 0:
                return

            # Pump.fun stores amounts as raw lamports and raw token units (6 dec)
            # price_sol = (vs / 1e9) / (vt / 1e6)
            sol_amount   = vs / 1e9
            token_amount = vt / 1e6
            if token_amount == 0:
                return

            price_usd = (sol_amount / token_amount) * self._sol_usd
            self._write_price(token_lower, price_usd, source="pump-curve")

        except Exception as e:
            logger.debug(f"[PoolFeed] Pump.fun decode error: {e}")
            await self._on_unknown(token_lower)

    # ───────────────────────── fallback ──────────────────────────────────────

    async def _on_unknown(self, token_lower: str):
        """Pool type unknown — trigger immediate DexScreener re-fetch (better than 8s wait)."""
        entry = self._pools.get(token_lower)
        if not entry:
            return
        now = time.monotonic()
        if now - entry.last_fallback_ts < 1.5:
            return   # debounce
        entry.last_fallback_ts = now
        self.fallback_triggers += 1
        try:
            asyncio.create_task(self._fallback(token_lower))
        except Exception as e:
            logger.debug(f"[PoolFeed] Fallback task error: {e}")

    # ───────────────────────── price write ────────────────────────────────────

    def _write_price(self, token_lower: str, price_usd: float, source: str = ""):
        """Write computed price into the shared DexScreener cache."""
        if price_usd <= 0:
            return

        # Sanity check: reject implausible jumps
        existing = self._price_cache.get(token_lower, {}).get("price", 0)
        if existing > 0:
            ratio = price_usd / existing
            if ratio > _SANITY_MAX_JUMP or ratio < (1.0 / _SANITY_MAX_JUMP):
                logger.debug(
                    f"[PoolFeed] Sanity rejected {price_usd:.8f} "
                    f"(existing={existing:.8f} ratio={ratio:.1f}×)"
                )
                return

        # Debounce
        now = time.monotonic()
        if now - self._last_write.get(token_lower, 0) < _DEBOUNCE_SECS:
            return
        self._last_write[token_lower] = now

        # Update cache — preserve volume stats from last DexScreener fetch.
        # pool_price_ts is written ONLY by PoolPriceFeed so _refresh_volume_for
        # can distinguish on-chain prices from stale DexScreener prices.
        cache = self._price_cache.get(token_lower, {})
        cache["price"]         = price_usd
        cache["ts"]            = now
        cache["pool_price_ts"] = now   # sentinel: pool feed owns this price
        self._price_cache[token_lower] = cache

        self.on_chain_prices += 1
        logger.debug(
            f"[PoolFeed] ⚡ {source} price {token_lower[:12]}… "
            f"= ${price_usd:.8f} (#{self.on_chain_prices})"
        )

        # Fire realtime stop + TP checks on each decoded tick. This is the
        # critical path that solves the DexScreener-indexer-lag problem
        # (RAGEGUY 2026-05-15: real pool spiked +13.5% in 60s, bot saw +1.1%
        # because the indexed-tokens API lagged the real pumpswap pool).
        if self.position_manager is not None:
            try:
                self.position_manager.check_stop_loss_realtime(token_lower, price_usd)
                self.position_manager.check_take_profit_realtime(token_lower, price_usd)
            except Exception as _e:
                logger.debug(f"[PoolFeed] pm realtime hook err: {_e}")


# ─────────────────────────── helpers ──────────────────────────────────────────

def _decode_spl_amount(data_b64: str) -> Optional[float]:
    """
    SPL token account layout:
      [0-31]  mint
      [32-63] owner
      [64-71] amount (u64 LE)  ← raw token balance
    Returns the raw amount as a float, or None on error.
    """
    try:
        raw = base64.b64decode(data_b64)
        if len(raw) < 72:
            return None
        return float(struct.unpack_from('<Q', raw, 64)[0])
    except Exception:
        return None
