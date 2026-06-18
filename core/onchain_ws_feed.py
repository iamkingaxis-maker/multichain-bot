"""accountSubscribe WebSocket price feed for a SMALL hot subset of mints.

Task B3 of the free-realtime-price-feeds plan. The TRUE ~1-2s precision layer:
for the armed/open set (<=~80 mints) we subscribe to each token's pump.fun
bonding-curve account via the FREE public Solana RPC WebSocket (accountSubscribe),
decode price on each push notification, convert to USD, and write an in-memory
price cache (~0.4-1s fresh).

BEST-EFFORT and NEVER the sole price source -- nothing reads this cache yet (B4
wires it; until then this is money-path-safe by isolation). Flag-gated:
ONCHAIN_WS_MODE in {off, shadow, on}, default off => run() is a true no-op
(no sockets opened).

MEASURED facts (free-feed bakeoff RPC probe):
- public RPC wss://api.mainnet-beta.solana.com accepts ~100 subs/connection then
  closes with code 1013 -> use <=90 subs/conn, multiple connections for the subset.
- notifications are base64 account data; decode via resolve_price_account.
- price_usd = price_sol * sol_usd.

The decode/handle/plan logic is SYNC and unit-testable; only the socket loop is
async and is exercised at runtime (B4), not in unit tests.
"""

import asyncio
import base64
import logging
import os
import time

from core.onchain_price import bonding_curve_pda, resolve_price_account

logger = logging.getLogger(__name__)

DEFAULT_WS_RPC_URL = "wss://api.mainnet-beta.solana.com"
SUBS_PER_CONN = 90          # <=90 subs/conn (public RPC closes ~100 with code 1013)
_CLOSE_CODE_TOO_MANY = 1013

# JSON-RPC commitment for accountSubscribe pushes (cheapest/freshest).
_COMMITMENT = "processed"


class OnchainWsFeed:
    """WS accountSubscribe feed over the pump.fun bonding-curve PDAs of a hot subset."""

    def __init__(self, get_sol_usd, rpc_ws_url=None):
        """get_sol_usd: zero-arg callable returning the current SOL/USD price.
        rpc_ws_url: override; defaults to env WS_RPC_URL then the public RPC.
        """
        self.get_sol_usd = get_sol_usd
        self.rpc_ws_url = (
            rpc_ws_url
            or os.environ.get("WS_RPC_URL")
            or DEFAULT_WS_RPC_URL
        )

        # address-keyed (lowercased) caches
        self.price_cache = {}   # mint_lower -> usd
        self.ts = {}            # mint_lower -> epoch seconds
        self.migrated_skips = 0

        # pda(str) -> mint(original-case) routing map, built in run()
        self._pda_to_mint = {}
        # last run took the no-op path (mode off) -- testable without sockets
        self.last_run_was_noop = False
        self._stop = False

    # --- mode -----------------------------------------------------------------

    @staticmethod
    def _mode():
        return os.environ.get("ONCHAIN_WS_MODE", "off").strip().lower()

    # --- planning (SYNC, testable) -------------------------------------------

    def _plan_connections(self, mints, per_conn=SUBS_PER_CONN):
        """Chunk mints into connection groups of <=per_conn subscriptions each."""
        return [mints[i:i + per_conn] for i in range(0, len(mints), per_conn)]

    # --- decode/handle (SYNC, testable, exception-safe) ----------------------

    def _handle_account_data(self, mint, b64_data):
        """Decode one base64 account blob for `mint` and update the cache.

        Migrated curve -> increment migrated_skips, no write. Any error is caught
        (never raises) so a single bad notification can't crash the socket loop.
        """
        try:
            if not mint or not b64_data:
                return
            try:
                raw = base64.b64decode(b64_data, validate=True)
            except Exception:
                return

            resolved = resolve_price_account(mint, raw)
            kind = resolved.get("kind")

            if kind == "migrated":
                self.migrated_skips += 1
                return
            if kind != "bonding":
                return

            price_sol = resolved.get("price_sol")
            if not price_sol or price_sol <= 0:
                return

            sol_usd = self.get_sol_usd()
            if not sol_usd or sol_usd <= 0:
                return

            usd = price_sol * sol_usd
            if usd <= 0:
                return

            key = mint.lower()
            self.price_cache[key] = usd
            self.ts[key] = time.time()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] handle error for %s: %s", mint, e)

    def get_price(self, mint):
        """Return (usd, ts) for a mint, or None if not cached."""
        if not mint:
            return None
        key = mint.lower()
        if key in self.price_cache:
            return (self.price_cache[key], self.ts.get(key, 0.0))
        return None

    def stop(self):
        self._stop = True

    # --- async socket I/O (runtime; not unit-tested) -------------------------

    async def run(self, mints):
        """Subscribe to the bonding-curve PDAs of `mints` and stream prices.

        TRUE no-op when ONCHAIN_WS_MODE is off (default) -- returns immediately,
        opens NO sockets. In shadow/on mode, opens the planned connections and
        keeps them alive best-effort (any error caught + retried with backoff).
        """
        if self._mode() == "off":
            self.last_run_was_noop = True
            logger.info("[onchain-ws] ONCHAIN_WS_MODE=off -> no-op (no sockets opened)")
            return

        self.last_run_was_noop = False
        self._stop = False

        # Build pda->mint routing for notification dispatch.
        self._pda_to_mint = {}
        valid = []
        for m in mints:
            try:
                pda = bonding_curve_pda(m)
            except Exception as e:
                logger.debug("[onchain-ws] PDA derive failed for %s: %s", m, e)
                continue
            self._pda_to_mint[pda] = m
            valid.append(m)

        chunks = self._plan_connections(valid)
        logger.info(
            "[onchain-ws] mode=%s subset=%d connections=%d (<=%d subs each)",
            self._mode(), len(valid), len(chunks), SUBS_PER_CONN,
        )
        if not chunks:
            return

        await asyncio.gather(
            *(self._connection_loop(chunk) for chunk in chunks),
            return_exceptions=True,
        )

    async def _connection_loop(self, mint_chunk):
        """Maintain one WS connection for a chunk; reconnect on close/1013/error."""
        try:
            import websockets
        except Exception:  # pragma: no cover - dependency note
            logger.warning(
                "[onchain-ws] `websockets` not importable -- WS feed disabled. "
                "Install websockets (already used by AxiomPriceFeed)."
            )
            return

        backoff = 1.0
        while not self._stop:
            try:
                async with websockets.connect(
                    self.rpc_ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=None,
                ) as ws:
                    backoff = 1.0  # reset on a successful connect
                    sub_id_to_pda = await self._subscribe_chunk(ws, mint_chunk)
                    await self._consume(ws, sub_id_to_pda)
            except Exception as e:
                code = getattr(e, "code", None)
                if code == _CLOSE_CODE_TOO_MANY:
                    logger.warning(
                        "[onchain-ws] code 1013 (too many subs) -- reconnecting chunk(%d)",
                        len(mint_chunk),
                    )
                else:
                    logger.debug("[onchain-ws] connection error (chunk=%d): %s",
                                 len(mint_chunk), e)
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _subscribe_chunk(self, ws, mint_chunk):
        """Send accountSubscribe for each mint's PDA. Returns subscription_id->pda."""
        import json

        pending = {}   # request_id -> pda
        sub_id_to_pda = {}  # subscription_id -> pda
        req_id = 1
        for m in mint_chunk:
            pda = bonding_curve_pda(m)
            self._pda_to_mint[pda] = m
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "accountSubscribe",
                "params": [pda, {"encoding": "base64", "commitment": _COMMITMENT}],
            }
            pending[req_id] = pda
            await ws.send(json.dumps(msg))
            req_id += 1

        # Drain subscription confirmations (best-effort; bounded).
        for _ in range(len(pending)):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            except Exception:
                break
            try:
                resp = json.loads(raw)
            except Exception:
                continue
            rid = resp.get("id")
            if rid in pending and "result" in resp:
                sub_id_to_pda[resp["result"]] = pending[rid]
        return sub_id_to_pda

    async def _consume(self, ws, sub_id_to_pda):
        """Read accountNotification frames and route to _handle_account_data."""
        import json

        while not self._stop:
            raw = await ws.recv()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("method") != "accountNotification":
                continue
            params = msg.get("params") or {}
            sub = params.get("subscription")
            pda = sub_id_to_pda.get(sub)
            if pda is None:
                continue
            mint = self._pda_to_mint.get(pda)
            if mint is None:
                continue
            try:
                value = (params.get("result") or {}).get("value") or {}
                data = value.get("data")
                # data is [base64_str, "base64"] for base64 encoding
                b64 = data[0] if isinstance(data, (list, tuple)) and data else data
            except Exception:
                continue
            self._handle_account_data(mint, b64)
