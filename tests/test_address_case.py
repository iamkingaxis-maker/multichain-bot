"""
Regression test: Solana mint addresses must NEVER be lowercased before being
passed to Jupiter or RPC.

Root cause of 2026-04-29 incident: Trader.sell() lowercased token_address
before sending to Jupiter, which rejected the lowercased string with
"WrongSize" because it was no longer a valid base58-encoded 32-byte pubkey.
Result: every TP/stop/manual-sell failed silently for 19 hours while the bot
held positions through the floor.

These tests do NOT call Jupiter or submit transactions — they verify the
in-process invariants that the position records keep original-case mints
and that sell-path arguments are case-preserved.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.trader import Trader, Position


# Real Solana mints — both case-mixed by base58 design
BURNIE_MINT = "CGEDT9QZDvvH5GmVkWJH2BXiMJqMJySC9ihWyr7Spump"
TRIPLET_MINT = "J8PSdNP3QewKq2Z1JJJFDMaqF7KcaiJhR7gbr5KZpump"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class _StubTracker:
    def __init__(self):
        self.buys = []
        self.sells = []

    def record_buy(self, position):
        self.buys.append(position)

    def record_sell(self, *args, **kwargs):
        self.sells.append({"args": args, "kwargs": kwargs})

    def is_rugged(self, addr):
        return False


class _StubTelegram:
    async def send(self, *a, **kw):
        pass


class _StubRiskManager:
    def record_buy(self, *a, **kw):
        pass

    def record_sell(self, *a, **kw):
        pass

    def can_trade(self, *a, **kw):
        return True


class _StubKillSwitch:
    is_active = False
    _kill_reason = ""


def _make_trader():
    """Build a Trader for tests — paper mode (empty private key) to avoid
    any RPC/Jupiter network calls."""
    return Trader(
        private_key="",
        rpc_url="https://example.invalid",
        tracker=_StubTracker(),
        telegram=_StubTelegram(),
        risk_manager=_StubRiskManager(),
        kill_switch=_StubKillSwitch(),
    )


def test_position_preserves_mint_case():
    """A Position created with mixed-case mint must store mixed case in
    token_address.  This is what the live buy path uses for Jupiter calls."""
    p = Position(
        token_address=BURNIE_MINT,
        token_symbol="BURNIE",
        entry_price_usd=0.0156,
        amount_tokens=2897.32,
        amount_sol_spent=0.5396,
        entry_time=datetime.now(timezone.utc),
        reason="test",
    )
    assert p.token_address == BURNIE_MINT, (
        f"Position.token_address must preserve case, got {p.token_address}"
    )
    print(f"  PASS: Position.token_address preserves case ({p.token_address})")


def test_open_positions_dict_lookup_case_insensitive():
    """Looking up a position by lowercase OR original case must find it.
    The dict uses lowercase keys (so lookups always use .lower()) but the
    Position object holds the original case."""
    trader = _make_trader()
    p = Position(
        token_address=BURNIE_MINT,
        token_symbol="BURNIE",
        entry_price_usd=0.0156,
        amount_tokens=2897.32,
        amount_sol_spent=0.5396,
        entry_time=datetime.now(timezone.utc),
        reason="test",
    )
    trader.open_positions[BURNIE_MINT.lower()] = p

    # Lookup with lowercase (the canonical dict key)
    found = trader.open_positions.get(BURNIE_MINT.lower())
    assert found is p, "Lowercase lookup failed"

    # Found position must still expose original-case token_address
    assert found.token_address == BURNIE_MINT, (
        f"Position.token_address corrupted to {found.token_address}"
    )
    print(f"  PASS: Dict lookup case-insensitive, mint case preserved")


async def _run_async_tests():
    """Async tests — exercise sell() up to but not including network calls."""
    trader = _make_trader()
    p = Position(
        token_address=BURNIE_MINT,
        token_symbol="BURNIE",
        entry_price_usd=0.0156,
        amount_tokens=2897.32,
        amount_sol_spent=0.5396,
        entry_time=datetime.now(timezone.utc),
        reason="test",
        token_decimals=6,
    )
    trader.open_positions[BURNIE_MINT.lower()] = p

    # Inject a spying _get_quote so we can capture what the sell path
    # would have sent to Jupiter — without actually calling Jupiter.
    captured = {"input_mint": None, "output_mint": None}

    async def spy_quote(input_mint, output_mint, amount, slippage_bps=100):
        captured["input_mint"] = input_mint
        captured["output_mint"] = output_mint
        # Return None so sell aborts after capture (no further side effects).
        return None

    trader._get_quote = spy_quote

    # Call sell with the lowercase address (simulating position_manager
    # iterating the lowercased dict).  The fix must look up the case-
    # preserved mint from the Position and use THAT for the Jupiter quote.
    # Don't pass paper-mode private_key to avoid the paper short-circuit;
    # set it temporarily so we hit the live-sell path.
    trader.private_key = "fake-key-for-test"  # forces live path; spy intercepts before any RPC
    try:
        # We expect this to fail at the spy (returns None → 3 retries → return)
        await trader.sell(BURNIE_MINT.lower(), "BURNIE", "test sell", pct=1.0)
    except Exception as e:
        # OK — execution path past the spy may hit other live-mode dependencies
        pass
    finally:
        trader.private_key = ""

    # Assert: the input_mint sent to Jupiter must be ORIGINAL CASE
    assert captured["input_mint"] is not None, "Spy quote was never called"
    assert captured["input_mint"] == BURNIE_MINT, (
        f"BUG: sell() sent lowercased mint to Jupiter.\n"
        f"  Sent:     {captured['input_mint']}\n"
        f"  Expected: {BURNIE_MINT}"
    )
    print(f"  PASS: sell() sends ORIGINAL case mint to Jupiter ({captured['input_mint']})")


def main():
    print("Running address-case regression tests...\n")
    failures = 0
    for name, fn in [
        ("position_preserves_mint_case", test_position_preserves_mint_case),
        ("open_positions_dict_lookup_case_insensitive", test_open_positions_dict_lookup_case_insensitive),
    ]:
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL [{name}]: {e}")
            failures += 1
        except Exception as e:
            print(f"  ERROR [{name}]: {type(e).__name__}: {e}")
            failures += 1

    # Async tests
    try:
        asyncio.run(_run_async_tests())
    except AssertionError as e:
        print(f"  FAIL [async sell case-preservation]: {e}")
        failures += 1
    except Exception as e:
        print(f"  ERROR [async sell case-preservation]: {type(e).__name__}: {e}")
        failures += 1

    print()
    if failures == 0:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"{failures} TEST(S) FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
