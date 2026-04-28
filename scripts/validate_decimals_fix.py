"""
Read-only validation of the decimals fix.  No swaps, no signing — pure RPC
getAccountInfo lookups against known tokens.  Exercises the same code path
the live trader will hit at buy time.

Goal: confirm `Trader._get_token_decimals` returns the correct value for:
  - 6-decimal pump.fun tokens (the bug class that bit us with TripleT)
  - 6-decimal stable/established SPL tokens (USDC, USDT)
  - 9-decimal tokens (SOL, BONK is 5 actually — picking real 9-decimal targets)
  - A non-existent mint (should fall back to 6)

Then walks through the math: given out_amount=X and decimals=D, what does the
buy path now record vs what it WOULD have recorded with the old hardcoded 1e9.
"""
import asyncio
import os
import sys

# Make the local trader code importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.trader import Trader


# Known decimals (from on-chain, hand-verified)
TEST_CASES = [
    # mint, symbol, expected_decimals
    ("So11111111111111111111111111111111111111112",  "SOL",     9),
    ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  "USDC",    6),
    ("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  "USDT",    6),
    ("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  "BONK",    5),
    ("JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",   "JUP",     6),
    ("J8PSdNP3QewKq2Z1JJJFDMaqF7KcaiJhR7gbr5KZpump",  "TripleT", 6),  # the token that broke us
    ("EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  "WIF",     6),
    ("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",  "(invalid)",  -1),  # bad mint, expect fallback=6
]


class _StubKillSwitch:
    is_active = False
    _kill_reason = ""


class _StubTracker:
    def record_buy(self, *a, **kw): pass
    def record_sell(self, *a, **kw): pass


class _StubTelegram:
    async def send(self, *a, **kw): pass


class _StubRiskManager:
    def record_buy(self, *a, **kw): pass
    def record_sell(self, *a, **kw): pass


async def main():
    rpc_url = os.environ.get(
        "SOLANA_RPC_URL",
        "https://mainnet.helius-rpc.com/?api-key=06c97f31-8c26-4dae-9fb7-2f32ccc87f2c",
    )

    # Build a Trader stub with no private key (paper mode) so we don't
    # accidentally do anything stateful.  We're only exercising
    # _get_token_decimals which is read-only.
    trader = Trader(
        private_key="",
        rpc_url=rpc_url,
        tracker=_StubTracker(),
        telegram=_StubTelegram(),
        risk_manager=_StubRiskManager(),
        kill_switch=_StubKillSwitch(),
    )

    print(f"{'Symbol':<10} {'Mint (prefix)':<22} {'Expected':<10} {'Got':<6} {'Result':<10}")
    print("-" * 70)
    pass_count = 0
    fail_count = 0
    for mint, sym, expected in TEST_CASES:
        got = await trader._get_token_decimals(mint)
        if expected == -1:
            # Invalid mint — should fall back to 6
            ok = (got == 6)
            label = "PASS (fallback to 6)" if ok else "FAIL"
        else:
            ok = (got == expected)
            label = "PASS" if ok else "FAIL"
        if ok:
            pass_count += 1
        else:
            fail_count += 1
        print(f"{sym:<10} {mint[:20]+'...':<22} {str(expected):<10} {str(got):<6} {label}")

    print("-" * 70)
    print(f"Passed: {pass_count}/{len(TEST_CASES)}  Failed: {fail_count}")
    print()

    # Walk through the math for the TripleT case (the actual buy that bit us)
    print("=== TripleT replay: what the new code records vs old ===")
    out_amount = 12_509_814_354  # exact raw amount we received on-chain
    decimals = 6                  # actual TripleT decimals
    position_size_usd = 45.0

    new_amount_tokens = out_amount / (10 ** decimals)
    new_entry_price = position_size_usd / new_amount_tokens
    old_amount_tokens = out_amount / 1e9
    old_entry_price = position_size_usd / old_amount_tokens

    print(f"  out_amount (raw atomic):  {out_amount:,}")
    print(f"  decimals:                 {decimals}")
    print(f"  position_size_usd:        ${position_size_usd}")
    print()
    print(f"  NEW code:  amount_tokens={new_amount_tokens:>14,.4f}  entry_price=${new_entry_price:.10f}")
    print(f"  OLD code:  amount_tokens={old_amount_tokens:>14,.4f}  entry_price=${old_entry_price:.10f}")
    print(f"  ratio:     {old_amount_tokens / new_amount_tokens:.4f}× (1000× off, matches the bug)")

    print()
    if fail_count == 0:
        print("✅ All decimals lookups passed — fix is correct against live RPC.")
    else:
        print("❌ Some decimals lookups failed — investigate before unpausing.")


if __name__ == "__main__":
    asyncio.run(main())
