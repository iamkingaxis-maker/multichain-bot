"""
Graduation Sniper — dry-run test.

Runs the sniper in detection-only mode (no real buys).
Connects to the RPC WebSocket and logs every graduation detected.
Run for 5-10 minutes and verify:
  1. WebSocket connects without error
  2. Graduations are detected (pump.fun graduates several tokens per hour)
  3. Mints are extracted correctly
  4. Jupiter quotes return successfully
  5. Price impact is in expected range

Usage:
    python test_graduation_sniper.py

Env vars required:
    SOLANA_RPC_URL   — already set in Railway / .env
"""

import asyncio
import json
import logging
import os
import sys
import time

# Allow import from project root
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
# suppress noisy aiohttp internals
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from feeds.graduation_sniper import (
    GraduationSniper,
    PUMP_FUN_PROGRAM, PUMP_MIGRATION_PROG, PUMPSWAP_PROGRAM,
    GRADUATE_LOG, SOL_MINT,
)

RPC_URL = os.environ.get("SOLANA_RPC_URL", "")
if not RPC_URL:
    logger.error("SOLANA_RPC_URL not set — check .env or environment")
    sys.exit(1)


class MockTrader:
    """Stub trader — logs the buy call instead of executing it."""
    open_positions = {}

    async def buy(self, token_address, token_symbol, reason, **kwargs):
        logger.info(
            f"\n{'='*60}\n"
            f"  [DRY RUN BUY] {token_symbol}\n"
            f"  Mint:   {token_address}\n"
            f"  USD:    ${kwargs.get('override_usd', '?')}\n"
            f"  Reason: {reason}\n"
            f"{'='*60}\n"
        )


class TestSniper(GraduationSniper):
    """Subclass that logs every step in detail."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._raw_msg_count = 0

    async def _on_message(self, raw: str):
        self._raw_msg_count += 1
        if self._raw_msg_count <= 3 or self._raw_msg_count % 500 == 0:
            logger.debug(f"[TEST] Raw msg #{self._raw_msg_count}: {raw[:120]}")
        # Flag any Withdraw instruction so we know detection would fire
        from feeds.graduation_sniper import GRADUATE_LOG, PUMPSWAP_PROGRAM
        if GRADUATE_LOG in raw:
            logger.info(
                f"[TEST] 🔍 Withdraw seen! PumpSwap={'yes' if PUMPSWAP_PROGRAM in raw else 'no'} "
                f"— sig: {raw[raw.find('signature')+12:raw.find('signature')+58]}"
            )
        await super()._on_message(raw)

    async def _handle_graduation(self, sig: str, grad_type: str):
        t0 = time.time()
        logger.info(f"[TEST] Graduation tx: {sig}")

        mint = await self._extract_mint(sig)
        t_mint = time.time() - t0

        if not mint:
            logger.warning(f"[TEST] ❌ Could not extract mint ({t_mint*1000:.0f}ms)")
            return

        logger.info(f"[TEST] ✅ Mint: {mint} (extracted in {t_mint*1000:.0f}ms)")

        # Jupiter quote
        quote = None
        for attempt in range(5):
            quote = await self._jupiter_quote(mint)
            elapsed = (time.time() - t0) * 1000
            if quote is not None:
                logger.info(
                    f"[TEST] ✅ Jupiter routable (attempt {attempt+1}, {elapsed:.0f}ms)"
                )
                break
            logger.info(f"[TEST] ⏳ Jupiter attempt {attempt+1} failed — retrying…")
            if attempt < 4:
                await asyncio.sleep(1.0)

        if quote is None:
            elapsed = (time.time() - t0) * 1000
            logger.warning(f"[TEST] ❌ Jupiter: not routable after 5 attempts ({elapsed:.0f}ms)")
            self.buys_skipped_noroute += 1
            return

        impact = abs(float(quote.get("priceImpactPct") or 0)) * 100
        out_amt = int(quote.get("outAmount") or 0)
        in_amt  = int(quote.get("inAmount") or 0)
        logger.info(
            f"[TEST] Quote: impact={impact:.2f}% | "
            f"in={in_amt} lamports → out={out_amt} tokens"
        )

        if impact > self.max_price_impact:
            logger.info(f"[TEST] ⚠️  High impact — would skip in live mode")
            self.buys_skipped_impact += 1
            return

        # Call mock buy
        await self.trader.buy(
            token_address=mint,
            token_symbol=f"GRAD-{mint[:6]}",
            reason=f"Graduation sniper ({grad_type}) — DRY RUN",
            signal_score=55,
            override_usd=self.position_usd,
            chain_id="solana",
        )
        self.buys_attempted += 1


async def print_stats(sniper: TestSniper):
    """Print stats every 60 seconds."""
    start = time.time()
    while True:
        await asyncio.sleep(60)
        elapsed = (time.time() - start) / 60
        stats = sniper.get_stats()
        logger.info(
            f"\n[STATS after {elapsed:.0f}min]\n"
            f"  Raw WS messages      : {sniper._raw_msg_count}\n"
            f"  Graduations detected : {stats['graduations_detected']}\n"
            f"  Buys attempted       : {stats['buys_attempted']}\n"
            f"  Skipped (no route)   : {stats['skipped_no_route']}\n"
            f"  Skipped (impact)     : {stats['skipped_impact']}\n"
            f"  Skipped (dup)        : {stats['skipped_dup']}\n"
        )


async def main():
    logger.info(f"RPC: {RPC_URL[:50]}…")
    logger.info("Starting graduation sniper test — Ctrl+C to stop\n")

    sniper = TestSniper(
        rpc_url=RPC_URL,
        trader=MockTrader(),
        position_usd=40.0,
        max_price_impact_pct=10.0,
        sol_price_usd=150.0,
    )

    await asyncio.gather(
        sniper.run(),
        print_stats(sniper),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Test stopped")
