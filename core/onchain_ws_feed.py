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

from core.onchain_amm import (
    DEFAULT_BASE_DECIMALS,
    PUMP_AMM_PROGRAM_ID,
    WSOL_MINT,
    decode_mint_decimals,
    decode_pumpswap_pool,
    decode_token_account,
    price_sol_from_vaults,
    pumpswap_pool_pda,
)
from core.onchain_price import bonding_curve_pda, resolve_price_account

logger = logging.getLogger(__name__)

DEFAULT_WS_RPC_URL = "wss://api.mainnet-beta.solana.com"
DEFAULT_HTTP_RPC_URL = "https://api.mainnet-beta.solana.com"
SUBS_PER_CONN = 90          # <=90 subs/conn (public RPC closes ~100 with code 1013)
_CLOSE_CODE_TOO_MANY = 1013

# JSON-RPC commitment for accountSubscribe pushes (cheapest/freshest).
_COMMITMENT = "processed"


def _env_int(name, default):
    """Parse a positive int env var; fall back to default on bad/missing."""
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except Exception:
        return default


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
        # HTTP RPC (same free public endpoint family) -- used ONLY by the
        # migrated-token pool resolver (task #493), a few small batched
        # getMultipleAccounts calls per refresh. Never a secret.
        self.rpc_http_url = (
            os.environ.get("SOLANA_RPC_URL")
            or DEFAULT_HTTP_RPC_URL
        )

        # address-keyed (lowercased) caches
        self.price_cache = {}   # mint_lower -> usd
        self.ts = {}            # mint_lower -> epoch seconds
        self.migrated_skips = 0

        # pda(str) -> mint(original-case) routing map, built in run()
        self._pda_to_mint = {}
        # live connection-loop tasks keyed by stable chunk key (sorted lower
        # tuple of the chunk's mints). Managed dynamically by the supervisor so
        # the open socket set tracks the rotating hot set instead of freezing at
        # boot. {} until run() opens connections.
        self._conn_tasks = {}
        # currently-tracked hot mint set (lowercased) -- what we believe is
        # subscribed. Maintained by the refresh loop so coverage tracks rotation.
        self._tracked = set()
        # ws notifications received (any frame) -- heartbeat liveness counter.
        self.ws_msgs = 0
        # last run took the no-op path (mode off) -- testable without sockets
        self.last_run_was_noop = False
        self._stop = False

        # --- migrated-token AMM vault coverage (task #493) --------------------
        # All keyed by LOWERCASED mint unless noted. Behavior gated by
        # ONCHAIN_WS_MIGRATED_MODE (off/shadow/enforce, default off).
        self.amm_price_cache = {}     # mint_lower -> usd (served only in enforce)
        self.amm_ts = {}              # mint_lower -> epoch seconds
        self.amm_prices = 0           # count of AMM prices computed (observability)
        self._amm_pending = {}        # mint_lower -> ORIGINAL-case mint awaiting pool resolution
        self._amm_checked = set()     # mint_lower whose curve state was classified once
        self._amm_unsupported = set() # mint_lower with no usable canonical PumpSwap/WSOL pool
        self._amm_pools = {}          # mint_lower -> {mint, pool, base_vault, quote_vault,
                                      #                base_decimals, base_amount, quote_amount}
        self._amm_vault_route = {}    # vault_addr(ORIGINAL case) -> (mint_lower, 'base'|'quote')
        self._amm_conn_tasks = {}     # chunk key -> AMM vault connection-loop task

    # --- mode -----------------------------------------------------------------

    @staticmethod
    def _mode():
        return os.environ.get("ONCHAIN_WS_MODE", "off").strip().lower()

    @staticmethod
    def _migrated_mode():
        """ONCHAIN_WS_MIGRATED_MODE: off (default) / shadow / enforce.

        off     -- migrated mints only counted (pre-#493 behavior; no HTTP, no
                   extra subscriptions).
        shadow  -- AMM prices computed + cached in amm_price_cache for logging/
                   comparison (dip_scanner logs WS-MIGRATED shadow lines) but
                   NEVER served by get_price.
        enforce -- get_price serves AMM prices exactly like curve prices (curve
                   price wins if both exist; migrated curves stop pushing so in
                   practice AMM is the only source for migrated mints).
        """
        v = os.environ.get("ONCHAIN_WS_MIGRATED_MODE", "off").strip().lower()
        return v if v in ("off", "shadow", "enforce") else "off"

    # --- planning (SYNC, testable) -------------------------------------------

    def _plan_connections(self, mints, per_conn=SUBS_PER_CONN):
        """Chunk mints into connection groups of <=per_conn subscriptions each."""
        return [mints[i:i + per_conn] for i in range(0, len(mints), per_conn)]

    @staticmethod
    def _chunk_key(chunk):
        """Stable, order-insensitive key for a connection chunk (sorted lower tuple).

        Identical mint sets => identical key, so a refresh that yields the same
        chunk does NOT churn the live connection task.
        """
        try:
            return tuple(sorted((m or "").lower() for m in (chunk or []) if m))
        except Exception:  # pragma: no cover - defensive
            return tuple()

    def _reconcile_connection_chunks(self, desired_chunks, active_keys):
        """PURE reconciler: given the desired chunk list and the set of currently
        active connection-task keys, return (to_start, to_cancel).

        to_start = list of desired chunks (the chunk lists themselves) whose key
        is not yet active; to_cancel = list of active keys no longer desired.
        Side-effect free + exception-safe so the supervisor can call it on every
        refresh without risk of crashing the feed.
        """
        try:
            active = set(active_keys or set())
            desired_by_key = {}
            for chunk in (desired_chunks or []):
                k = self._chunk_key(chunk)
                if not k:
                    continue
                # first chunk wins for a given key (dedupe identical sets)
                desired_by_key.setdefault(k, chunk)
            to_start = [chunk for k, chunk in desired_by_key.items()
                        if k not in active]
            to_cancel = [k for k in active if k not in desired_by_key]
            return (to_start, to_cancel)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] reconcile error: %s", e)
            return ([], [])

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
                # #493: a curve push flipping to migrated (graduation happens
                # WHILE subscribed) queues the mint for AMM pool resolution.
                # Already-migrated-at-subscribe mints are caught by the
                # resolver's one-time curve classification instead (dead
                # curves never push).
                self._note_migrated(mint)
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
        """Return (usd, ts) for a mint, or None if not cached. THREAD-SAFE
        (2026-07-08): single .get() instead of `in`+`[]` — the old TOCTOU could
        raise KeyError when the feed runs on its own thread and _apply_refresh
        prunes the cache concurrently. Cached prices are always > 0 (writer drops
        usd<=0), so a None result unambiguously means 'not cached'.

        #493: in ONCHAIN_WS_MIGRATED_MODE=enforce a curve-cache miss falls
        through to the AMM cache (migrated mints). In off/shadow the AMM cache
        is NEVER served here (shadow reads go through get_amm_price)."""
        if not mint:
            return None
        key = mint.lower()
        usd = self.price_cache.get(key)
        if usd is not None:
            return (usd, self.ts.get(key, 0.0))
        if self._migrated_mode() == "enforce":
            usd = self.amm_price_cache.get(key)
            if usd is not None:
                return (usd, self.amm_ts.get(key, 0.0))
        return None

    def get_amm_price(self, mint):
        """Return (usd, ts) from the migrated-token AMM cache, or None.

        Mode-independent read for SHADOW validation (dip_scanner compares this
        to Jupiter and logs; it never drives a decision in shadow). Same
        thread-safe single-.get() pattern as get_price."""
        if not mint:
            return None
        key = mint.lower()
        usd = self.amm_price_cache.get(key)
        if usd is None:
            return None
        return (usd, self.amm_ts.get(key, 0.0))

    def stop(self):
        self._stop = True

    # --- migrated-token AMM coverage (SYNC, testable) -- task #493 ------------
    #
    # Offsets/derivation LIVE-VERIFIED 2026-07-10 (see core/onchain_amm.py
    # docstring + scripts/validate_ws_migrated.py). Flow:
    #   detect migrated mint -> resolve canonical PumpSwap pool (HTTP, once)
    #   -> subscribe pool base+quote VAULTS (WS) -> recompute price from the
    #   latest pair of vault balances on every push.
    # Raydium-only tokens have no canonical PumpSwap pool -> unsupported (v1).

    def _note_migrated(self, mint):
        """Queue a migrated mint for AMM pool resolution (exception-safe).

        No-op when ONCHAIN_WS_MIGRATED_MODE=off or the mint is already
        resolved/known-unsupported/queued."""
        try:
            if self._migrated_mode() == "off":
                return
            ml = (mint or "").lower()
            if not ml:
                return
            self._amm_checked.add(ml)
            if ml in self._amm_pools or ml in self._amm_unsupported:
                return
            self._amm_pending.setdefault(ml, mint)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] note_migrated error for %s: %s", mint, e)

    def _mark_amm_unsupported(self, mint_lower, reason):
        """Record a mint the AMM layer can't cover (fail-open; logged once)."""
        try:
            if mint_lower in self._amm_unsupported:
                return
            self._amm_unsupported.add(mint_lower)
            self._amm_pending.pop(mint_lower, None)
            logger.info(
                "[onchain-ws] WS-MIGRATED unsupported mint=%s reason=%s "
                "(falls back to Jupiter polling)", mint_lower, reason)
        except Exception:  # pragma: no cover - defensive
            pass

    def _register_amm_pool(self, mint, pool_addr, pool, base_decimals=None,
                           base_amount=None, quote_amount=None):
        """Register a resolved PumpSwap pool: vault routing + balance state.

        `pool` is decode_pumpswap_pool() output. Validates quote==WSOL and
        base_mint==mint (a mismatch means the derivation/decode is wrong for
        this token -> unsupported, never a wrong price). Returns True when
        registered. SYNC + exception-safe."""
        try:
            ml = (mint or "").lower()
            if not ml or not pool:
                return False
            if pool.get("quote_mint") != WSOL_MINT:
                self._mark_amm_unsupported(ml, "quote_mint!=WSOL")
                return False
            if pool.get("base_mint") != mint:
                self._mark_amm_unsupported(ml, "base_mint mismatch")
                return False
            base_vault = pool.get("base_vault")
            quote_vault = pool.get("quote_vault")
            if not base_vault or not quote_vault:
                self._mark_amm_unsupported(ml, "missing vaults")
                return False
            try:
                dec = int(base_decimals)
            except (TypeError, ValueError):
                dec = DEFAULT_BASE_DECIMALS
            self._amm_pools[ml] = {
                "mint": mint,
                "pool": pool_addr,
                "base_vault": base_vault,
                "quote_vault": quote_vault,
                "base_decimals": dec,
                "base_amount": base_amount,
                "quote_amount": quote_amount,
            }
            self._amm_vault_route[base_vault] = (ml, "base")
            self._amm_vault_route[quote_vault] = (ml, "quote")
            self._amm_pending.pop(ml, None)
            logger.info(
                "[onchain-ws] WS-MIGRATED pool resolved mint=%s pool=%s "
                "base_vault=%s quote_vault=%s dec=%d",
                mint, pool_addr, base_vault, quote_vault, dec)
            # Seed a first price when registration came with both balances.
            self._recompute_amm_price(ml)
            return True
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] register_amm_pool error for %s: %s", mint, e)
            return False

    def _handle_amm_vault_data(self, vault_addr, b64_data):
        """Decode one vault-account push and refresh that pool's price.

        Exception-safe like _handle_account_data: any bad frame is dropped,
        never raises into the socket loop."""
        try:
            route = self._amm_vault_route.get(vault_addr)
            if not route or not b64_data:
                return
            mint_lower, role = route
            state = self._amm_pools.get(mint_lower)
            if not state:
                return
            try:
                raw = base64.b64decode(b64_data, validate=True)
            except Exception:
                return
            tok = decode_token_account(raw)
            if not tok:
                return
            key = "base_amount" if role == "base" else "quote_amount"
            state[key] = tok["amount"]
            self._recompute_amm_price(mint_lower)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] amm handle error for %s: %s", vault_addr, e)

    def _recompute_amm_price(self, mint_lower):
        """Recompute + cache the USD price from the latest vault balances.

        Writes ONLY amm_price_cache/amm_ts -- get_price decides (by mode)
        whether that is ever served. Same sol_usd>0 and usd>0 gates as the
        curve path. Exception-safe."""
        try:
            state = self._amm_pools.get(mint_lower)
            if not state:
                return
            price_sol = price_sol_from_vaults(
                state.get("base_amount"), state.get("quote_amount"),
                state.get("base_decimals"))
            if not price_sol or price_sol <= 0:
                return
            sol_usd = self.get_sol_usd()
            if not sol_usd or sol_usd <= 0:
                return
            usd = price_sol * sol_usd
            if usd <= 0:
                return
            self.amm_price_cache[mint_lower] = usd
            self.amm_ts[mint_lower] = time.time()
            self.amm_prices += 1
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] amm price error for %s: %s", mint_lower, e)

    def _amm_desired_vault_chunks(self):
        """Connection plan for the AMM vault subscriptions (sorted for a
        stable chunk key). Exception-safe -> [] on error."""
        try:
            return self._plan_connections(sorted(self._amm_vault_route.keys()))
        except Exception:  # pragma: no cover - defensive
            return []

    def _prune_amm_for_dropped(self, dropped):
        """Prune all migrated-token state for mints that left the hot set.

        `dropped` is a set of LOWERCASED mints. checked/unsupported are also
        cleared so a re-added mint re-classifies (cheap: one batched RPC)."""
        try:
            for ml in dropped:
                self.amm_price_cache.pop(ml, None)
                self.amm_ts.pop(ml, None)
                self._amm_pending.pop(ml, None)
                self._amm_checked.discard(ml)
                self._amm_unsupported.discard(ml)
                state = self._amm_pools.pop(ml, None)
                if state:
                    self._amm_vault_route.pop(state.get("base_vault"), None)
                    self._amm_vault_route.pop(state.get("quote_vault"), None)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] amm prune error: %s", e)

    # --- refresh / heartbeat (SYNC, testable) --------------------------------

    def _heartbeat_line(self):
        """Build the unconditional heartbeat string (pure -> testable).

        Format is stable so silence-is-news monitoring can parse it.
        """
        try:
            sol = float(self.get_sol_usd() or 0.0)
        except Exception:
            sol = 0.0
        return (
            "[onchain] heartbeat mode=%s subs=%d cached=%d ws_msgs=%d sol_usd=%.4f"
            " mig_mode=%s amm_pools=%d amm_cached=%d"
            % (self._mode(), len(self._tracked), len(self.price_cache),
               int(self.ws_msgs), sol,
               self._migrated_mode(), len(self._amm_pools),
               len(self.amm_price_cache))
        )

    def _apply_refresh(self, new_mints):
        """Transition the tracked subscription set toward `new_mints` (SYNC,
        testable, exception-safe). Returns (added, dropped) lower-cased sets.

        v1 semantics: the tracked set BECOMES the new hot set. Caches and the
        pda->mint map for mints that fell out of the hot set are pruned so the
        cache footprint + reported coverage track the rotating armed/open set
        rather than growing unbounded. New mints are returned to the caller so
        the connection loops can accountSubscribe them; dropped mints stop being
        routed (their pda entry is removed) which is an effective unsubscribe.
        """
        try:
            new_lower = {m.lower() for m in (new_mints or []) if m}
        except Exception:
            new_lower = set()
        old = set(self._tracked)
        added = new_lower - old
        dropped = old - new_lower

        # Prune caches + routing for dropped mints (best-effort).
        if dropped:
            for pda, mint in list(self._pda_to_mint.items()):
                try:
                    if mint.lower() in dropped:
                        self._pda_to_mint.pop(pda, None)
                except Exception:
                    self._pda_to_mint.pop(pda, None)
            for k in list(self.price_cache.keys()):
                if k in dropped:
                    self.price_cache.pop(k, None)
                    self.ts.pop(k, None)
            # #493: migrated-token AMM state tracks the same hot set.
            self._prune_amm_for_dropped(dropped)

        self._tracked = new_lower
        return (added, dropped)

    # --- async socket I/O (runtime; not unit-tested) -------------------------

    @staticmethod
    def _resolve_mints(get_mints):
        """Accept either a callable (current hot list) or a static list.
        Returns a fresh list, exception-safe (empty on error)."""
        try:
            if callable(get_mints):
                return list(get_mints() or [])
            return list(get_mints or [])
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] get_mints error: %s", e)
            return []

    async def run(self, get_mints):
        """Subscribe to the bonding-curve PDAs of the hot set and stream prices.

        `get_mints` may be a CALLABLE returning the current hot mint list (the
        armed/open set, which rotates) OR a static list (back-compat). When a
        callable is given, a refresh loop re-points the subscription set every
        ONCHAIN_REFRESH_SECS so coverage tracks rotation instead of decaying.

        TRUE no-op when ONCHAIN_WS_MODE is off (default) -- returns immediately,
        opens NO sockets. In shadow/on mode, opens the planned connections and
        keeps them alive best-effort (any error caught + retried with backoff).
        An UNCONDITIONAL heartbeat logs liveness every ONCHAIN_HEARTBEAT_SECS.
        """
        if self._mode() == "off":
            self.last_run_was_noop = True
            logger.info("[onchain-ws] ONCHAIN_WS_MODE=off -> no-op (no sockets opened)")
            return

        self.last_run_was_noop = False
        self._stop = False

        # Initial hot set.
        mints = self._resolve_mints(get_mints)

        # Build pda->mint routing for notification dispatch + seed tracked set.
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
        self._tracked = {m.lower() for m in valid}

        chunks = self._plan_connections(valid)
        logger.info(
            "[onchain-ws] mode=%s subset=%d connections=%d (<=%d subs each)",
            self._mode(), len(valid), len(chunks), SUBS_PER_CONN,
        )

        # DYNAMIC supervisor: connection-loop tasks track the current tracked
        # set (re-chunked) instead of freezing at boot. Start the initial
        # desired chunks now; the refresh loop reconciles (start new / cancel
        # gone) as the hot set rotates. Boot-empty self-heals once mints arrive.
        self._conn_tasks = {}
        self._apply_chunk_reconcile(chunks)

        # Heartbeat + refresh run regardless of whether there are chunks yet --
        # silence-is-news + an empty startup set should still rotate in mints.
        supervisors = [self._heartbeat_loop()]
        if callable(get_mints):
            supervisors.append(self._refresh_loop(get_mints))
        # #493: migrated-token AMM resolver (classify curves once, resolve
        # PumpSwap pools, keep vault subscriptions reconciled). Only spawned
        # when the flag is on -- mode off costs literally nothing.
        if self._migrated_mode() != "off":
            supervisors.append(self._amm_resolver_loop())

        try:
            await asyncio.gather(*supervisors, return_exceptions=True)
        finally:
            # Tear down any live connection tasks on exit (stop()/cancellation).
            for key, task in list(self._conn_tasks.items()):
                try:
                    task.cancel()
                except Exception:
                    pass
            self._conn_tasks = {}
            for key, task in list(self._amm_conn_tasks.items()):
                try:
                    task.cancel()
                except Exception:
                    pass
            self._amm_conn_tasks = {}

    def _apply_chunk_reconcile(self, desired_chunks):
        """Reconcile live connection tasks toward `desired_chunks` (best-effort).

        Spawns a _connection_loop task for each newly-desired chunk and cancels
        tasks for chunks no longer desired. Fail-open: any error is logged and
        swallowed so a reconcile bug can never crash the feed. Also prunes tasks
        that have already finished (so a re-add re-spawns them).
        """
        try:
            # Drop finished tasks so their keys are eligible to restart.
            for key in list(self._conn_tasks.keys()):
                t = self._conn_tasks.get(key)
                if t is not None and t.done():
                    self._conn_tasks.pop(key, None)

            to_start, to_cancel = self._reconcile_connection_chunks(
                desired_chunks, set(self._conn_tasks.keys())
            )
            for key in to_cancel:
                task = self._conn_tasks.pop(key, None)
                if task is not None:
                    try:
                        task.cancel()
                    except Exception:
                        pass
            for chunk in to_start:
                key = self._chunk_key(chunk)
                if not key or key in self._conn_tasks:
                    continue
                try:
                    self._conn_tasks[key] = asyncio.ensure_future(
                        self._connection_loop(list(chunk))
                    )
                except Exception as e:  # pragma: no cover - defensive
                    logger.debug("[onchain-ws] start chunk failed: %s", e)
            if to_start or to_cancel:
                logger.info(
                    "[onchain-ws] connection reconcile +%d -%d live=%d",
                    len(to_start), len(to_cancel), len(self._conn_tasks),
                )
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] chunk reconcile error: %s", e)

    async def _heartbeat_loop(self):
        """Unconditional periodic heartbeat -> silence-is-news. Never raises."""
        secs = _env_int("ONCHAIN_HEARTBEAT_SECS", 30)
        while not self._stop:
            try:
                logger.info("%s", self._heartbeat_line())
                # SOL-gate observability: at boot SOL=0 -> every decode is
                # discarded; make the wait explicit instead of silent.
                try:
                    sol = float(self.get_sol_usd() or 0.0)
                except Exception:
                    sol = 0.0
                if sol <= 0:
                    logger.info("[onchain] waiting for SOL price (sol_usd=0 -> decodes discarded)")
            except Exception as e:  # pragma: no cover - defensive
                logger.debug("[onchain-ws] heartbeat error: %s", e)
            await asyncio.sleep(secs)

    async def _refresh_loop(self, get_mints):
        """Periodically re-point the subscription set as the hot set rotates.

        Best-effort: a refresh error is caught and never crashes the feed. v1
        (re)subscribes the current hot set on fresh connections and prunes the
        pda->mint map + caches for mints no longer hot (see _apply_refresh).
        """
        secs = _env_int("ONCHAIN_REFRESH_SECS", 60)
        while not self._stop:
            await asyncio.sleep(secs)
            if self._stop:
                break
            try:
                new_mints = self._resolve_mints(get_mints)
                added, dropped = self._apply_refresh(new_mints)
                if added or dropped:
                    logger.info(
                        "[onchain] refresh +%d -%d tracked=%d",
                        len(added), len(dropped), len(self._tracked),
                    )
                    # Re-derive pda routing for the (possibly new) tracked set so
                    # reconnecting connection loops subscribe the current hot set.
                    self._rebuild_pda_routing(new_mints)
                # Reconcile the live connection tasks against the current hot set
                # EVERY refresh (even when added/dropped is empty -- e.g. boot
                # started empty and the very first non-empty set should still
                # spawn connections; the reconciler is a no-op when stable).
                # Build desired chunks from the routable mints (those whose PDA
                # derived) keyed by original-case for accountSubscribe.
                desired = self._plan_connections(list(self._pda_to_mint.values()))
                self._apply_chunk_reconcile(desired)
            except Exception as e:
                logger.debug("[onchain-ws] refresh error: %s", e)

    def _rebuild_pda_routing(self, mints):
        """Rebuild _pda_to_mint for the given hot set (best-effort, sync)."""
        routing = {}
        for m in (mints or []):
            try:
                routing[bonding_curve_pda(m)] = m
            except Exception:
                continue
        self._pda_to_mint = routing

    # --- migrated-token AMM resolver + vault connections (async) -- task #493 -

    async def _rpc_get_multiple_accounts(self, addrs):
        """getMultipleAccounts over the free public HTTP RPC, run OFF-LOOP in
        a worker thread so a slow response can never stall the feed loop.

        Returns a list aligned with `addrs` (None per missing account) or None
        on transport/parse error. Fail-open; never raises."""
        if not addrs:
            return []

        def _post():
            import requests  # lazy: keep module import light
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getMultipleAccounts",
                "params": [list(addrs),
                           {"encoding": "base64", "commitment": "confirmed"}],
            }
            r = requests.post(self.rpc_http_url, json=payload, timeout=10)
            r.raise_for_status()
            return (r.json().get("result") or {}).get("value")

        try:
            return await asyncio.to_thread(_post)
        except Exception as e:
            logger.debug("[onchain-ws] getMultipleAccounts failed (%d addrs): %s",
                         len(addrs), e)
            return None

    async def _amm_resolver_loop(self):
        """Periodic migrated-token resolution + AMM connection reconcile.

        Best-effort: every ONCHAIN_MIGRATED_RESOLVE_SECS it (1) classifies
        not-yet-checked hot mints' bonding curves ONCE via one batched RPC
        call (dead/migrated curves never push, so WS alone can't detect them),
        (2) resolves queued migrated mints to their canonical PumpSwap pool +
        vaults (2 more batched calls), (3) reconciles the vault subscription
        connections. Any error is caught -- never crashes the feed."""
        secs = _env_int("ONCHAIN_MIGRATED_RESOLVE_SECS", 15)
        while not self._stop:
            try:
                await self._amm_resolve_once()
            except Exception as e:  # pragma: no cover - defensive
                logger.debug("[onchain-ws] amm resolver error: %s", e)
            await asyncio.sleep(secs)

    async def _amm_resolve_once(self):
        """One resolver pass (classify -> resolve -> reconcile). Fail-open."""
        if self._migrated_mode() == "off":
            return

        # (1) One-time curve classification for unchecked hot mints: a token
        # that migrated BEFORE we subscribed has a dead curve that never
        # pushes, so probe once over HTTP. <=90 per pass (RPC caps at 100).
        pairs = [(pda, m) for pda, m in list(self._pda_to_mint.items())
                 if m.lower() not in self._amm_checked][:90]
        if pairs:
            vals = await self._rpc_get_multiple_accounts([p for p, _ in pairs])
            if vals is not None and len(vals) == len(pairs):
                for (_pda, mint), v in zip(pairs, vals):
                    ml = mint.lower()
                    self._amm_checked.add(ml)
                    try:
                        if not v:
                            continue  # no curve account -> not a pump.fun token
                        data = v.get("data")
                        b64 = data[0] if isinstance(data, (list, tuple)) and data else data
                        raw = base64.b64decode(b64 or "")
                        if resolve_price_account(mint, raw).get("kind") == "migrated":
                            self._amm_pending.setdefault(ml, mint)
                    except Exception:
                        continue

        # (2) Resolve queued migrated mints -> canonical PumpSwap pool.
        pending = [(ml, m) for ml, m in list(self._amm_pending.items())
                   if ml not in self._amm_pools
                   and ml not in self._amm_unsupported][:20]
        if pending:
            pool_addrs = []
            for ml, mint in pending:
                try:
                    pool_addrs.append(pumpswap_pool_pda(mint))
                except Exception:
                    pool_addrs.append(None)
                    self._mark_amm_unsupported(ml, "pool pda derive failed")
            fetchable = [(i, a) for i, a in enumerate(pool_addrs) if a]
            vals = await self._rpc_get_multiple_accounts([a for _, a in fetchable])
            if vals is not None and len(vals) == len(fetchable):
                staged = []  # (ml, mint, pool_addr, decoded_pool)
                for (i, pool_addr), v in zip(fetchable, vals):
                    ml, mint = pending[i]
                    if not v:
                        # No canonical PumpSwap pool -> Raydium-only or odd
                        # migration; explicit v1 skip.
                        self._mark_amm_unsupported(ml, "no canonical pumpswap pool")
                        continue
                    if v.get("owner") != PUMP_AMM_PROGRAM_ID:
                        self._mark_amm_unsupported(ml, "pool owner mismatch")
                        continue
                    try:
                        data = v.get("data")
                        b64 = data[0] if isinstance(data, (list, tuple)) and data else data
                        decoded = decode_pumpswap_pool(base64.b64decode(b64 or ""))
                    except Exception:
                        decoded = None
                    if not decoded:
                        self._mark_amm_unsupported(ml, "pool decode failed")
                        continue
                    staged.append((ml, mint, pool_addr, decoded))

                # (2b) Seed vault balances + base decimals in ONE batched call
                # so the FIRST WS push already yields a complete price.
                if staged:
                    flat = []
                    for _ml, mint, _pa, dec_pool in staged:
                        flat += [dec_pool["base_vault"], dec_pool["quote_vault"], mint]
                    seed = await self._rpc_get_multiple_accounts(flat)
                    for idx, (ml, mint, pool_addr, dec_pool) in enumerate(staged):
                        base_amount = quote_amount = None
                        decimals = None
                        if seed is not None and len(seed) == len(flat):
                            base_amount, quote_amount, decimals = \
                                self._decode_amm_seed(seed[idx * 3:idx * 3 + 3])
                        self._register_amm_pool(
                            mint, pool_addr, dec_pool,
                            base_decimals=decimals,
                            base_amount=base_amount, quote_amount=quote_amount)

        # (3) Keep the vault subscription connections reconciled (also picks
        # up prunes done by _apply_refresh).
        self._apply_amm_chunk_reconcile(self._amm_desired_vault_chunks())

    @staticmethod
    def _decode_amm_seed(triple):
        """Decode a [base_vault, quote_vault, base_mint] getMultipleAccounts
        slice -> (base_amount, quote_amount, decimals); Nones on any miss.
        SYNC + exception-safe (pure decode -> testable)."""
        base_amount = quote_amount = decimals = None
        try:
            def _raw(v):
                if not v:
                    return None
                data = v.get("data")
                b64 = data[0] if isinstance(data, (list, tuple)) and data else data
                return base64.b64decode(b64 or "")

            bv, qv, mint_acct = (list(triple) + [None, None, None])[:3]
            tok = decode_token_account(_raw(bv))
            if tok:
                base_amount = tok["amount"]
            tok = decode_token_account(_raw(qv))
            if tok:
                quote_amount = tok["amount"]
            decimals = decode_mint_decimals(_raw(mint_acct))
        except Exception:  # pragma: no cover - defensive
            pass
        return (base_amount, quote_amount, decimals)

    def _apply_amm_chunk_reconcile(self, desired_chunks):
        """Reconcile AMM vault connection tasks (mirror of
        _apply_chunk_reconcile, separate task table + loop fn). Fail-open."""
        try:
            for key in list(self._amm_conn_tasks.keys()):
                t = self._amm_conn_tasks.get(key)
                if t is not None and t.done():
                    self._amm_conn_tasks.pop(key, None)

            to_start, to_cancel = self._reconcile_connection_chunks(
                desired_chunks, set(self._amm_conn_tasks.keys())
            )
            for key in to_cancel:
                task = self._amm_conn_tasks.pop(key, None)
                if task is not None:
                    try:
                        task.cancel()
                    except Exception:
                        pass
            for chunk in to_start:
                key = self._chunk_key(chunk)
                if not key or key in self._amm_conn_tasks:
                    continue
                try:
                    self._amm_conn_tasks[key] = asyncio.ensure_future(
                        self._amm_connection_loop(list(chunk))
                    )
                except Exception as e:  # pragma: no cover - defensive
                    logger.debug("[onchain-ws] start amm chunk failed: %s", e)
            if to_start or to_cancel:
                logger.info(
                    "[onchain-ws] amm connection reconcile +%d -%d live=%d",
                    len(to_start), len(to_cancel), len(self._amm_conn_tasks),
                )
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[onchain-ws] amm chunk reconcile error: %s", e)

    async def _amm_connection_loop(self, vault_chunk):
        """Maintain one WS connection subscribed to AMM vault accounts.

        Same shape/backoff as _connection_loop; frames route to
        _handle_amm_vault_data via the vault address."""
        try:
            import websockets
        except Exception:  # pragma: no cover - dependency note
            logger.warning("[onchain-ws] `websockets` not importable -- "
                           "migrated AMM coverage disabled.")
            return

        backoff = 1.0
        while not self._stop:
            try:
                async with websockets.connect(
                    self.rpc_ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=None,
                    compression=None,  # same loop-unstarve rationale as curve loop
                ) as ws:
                    backoff = 1.0
                    sub_id_to_addr = await self._subscribe_addresses(ws, vault_chunk)
                    await self._consume_amm(ws, sub_id_to_addr)
            except Exception as e:
                code = getattr(e, "code", None)
                if code == _CLOSE_CODE_TOO_MANY:
                    logger.warning(
                        "[onchain-ws] code 1013 (too many subs) -- reconnecting "
                        "amm chunk(%d)", len(vault_chunk))
                else:
                    logger.debug("[onchain-ws] amm connection error (chunk=%d): %s",
                                 len(vault_chunk), e)
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _subscribe_addresses(self, ws, addrs):
        """accountSubscribe raw addresses (no PDA derive). Returns sub_id->addr."""
        import json

        pending = {}   # request_id -> addr
        sub_id_to_addr = {}
        req_id = 1
        for addr in addrs:
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "accountSubscribe",
                "params": [addr, {"encoding": "base64", "commitment": _COMMITMENT}],
            }
            pending[req_id] = addr
            await ws.send(json.dumps(msg))
            req_id += 1

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
                sub_id_to_addr[resp["result"]] = pending[rid]
        return sub_id_to_addr

    async def _consume_amm(self, ws, sub_id_to_addr):
        """Read accountNotification frames and route to _handle_amm_vault_data."""
        import json

        while not self._stop:
            raw = await ws.recv()
            self.ws_msgs += 1
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("method") != "accountNotification":
                continue
            params = msg.get("params") or {}
            sub = params.get("subscription")
            addr = sub_id_to_addr.get(sub)
            if addr is None:
                continue
            try:
                value = (params.get("result") or {}).get("value") or {}
                data = value.get("data")
                b64 = data[0] if isinstance(data, (list, tuple)) and data else data
            except Exception:
                continue
            self._handle_amm_vault_data(addr, b64)

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
                    # LOOP-UNSTARVE (2026-07-08): DISABLE permessage-deflate. The
                    # websockets lib inflates compressed frames SYNCHRONOUSLY inside
                    # data_received/_read_ready; a market-wide burst of account
                    # notifications then inflates+parses in one callback = the ~9.5s
                    # loop freeze that starves detection. Uncompressed frames cost
                    # more inbound bandwidth (not billed) but zero on-loop inflate.
                    compression=None,
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
            self.ws_msgs += 1
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
