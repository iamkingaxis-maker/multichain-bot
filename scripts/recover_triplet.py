"""
One-off recovery script: sell stuck TripleT tokens back to SOL via Jupiter.

Background: live-mode buy at 22:16 UTC executed correctly on-chain (received
12,509.8 TripleT for $45 of SOL), but the bot's bookkeeping had a decimals
bug — recorded the position with 1e9 divisor instead of 1e6 — which made the
bot think it was at -99.9% loss. A subsequent restart wiped the position
from open_positions while the tokens remained on-chain.

This script reads the actual wallet balance, gets a Jupiter quote, signs with
the fixed signing path (VersionedTransaction(message, [signers])), and
submits the swap.
"""
import asyncio
import base64
import json
import os
import sys
from typing import Optional

import aiohttp
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction


SOL_MINT = "So11111111111111111111111111111111111111112"
TRIPLET_MINT = "J8PSdNP3QewKq2Z1JJJFDMaqF7KcaiJhR7gbr5KZpump"

PRIVATE_KEY = os.environ.get("SOLANA_PRIVATE_KEY", "").strip()
RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=06c97f31-8c26-4dae-9fb7-2f32ccc87f2c").strip()
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY", "").strip()

if not PRIVATE_KEY:
    print("ERROR: SOLANA_PRIVATE_KEY not set in env. Export it first.")
    sys.exit(1)

if JUPITER_API_KEY:
    JUP_QUOTE = "https://api.jup.ag/swap/v1/quote"
    JUP_SWAP = "https://api.jup.ag/swap/v1/swap"
    JUP_HEADERS = {"x-api-key": JUPITER_API_KEY}
else:
    JUP_QUOTE = "https://quote-api.jup.ag/v6/quote"
    JUP_SWAP = "https://quote-api.jup.ag/v6/swap"
    JUP_HEADERS = {}


async def post_rpc(payload: dict) -> Optional[dict]:
    async with aiohttp.ClientSession() as s:
        async with s.post(RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            return await r.json()


async def get_token_balance_raw(owner: str, mint: str) -> int:
    """Returns raw atomic units balance for the mint, or -1 on failure."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [owner, {"mint": mint}, {"encoding": "jsonParsed"}],
    }
    data = await post_rpc(payload) or {}
    accts = (data.get("result") or {}).get("value") or []
    if not accts:
        return 0
    total = 0
    for a in accts:
        info = a.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        total += int((info.get("tokenAmount") or {}).get("amount", "0"))
    return total


async def get_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 300):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": slippage_bps,
    }
    async with aiohttp.ClientSession(headers=JUP_HEADERS) as s:
        async with s.get(JUP_QUOTE, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                txt = await r.text()
                print(f"Quote HTTP {r.status}: {txt[:200]}")
                return None
            return await r.json()


async def execute_swap(quote: dict, keypair: Keypair) -> Optional[str]:
    """Returns tx signature on success, None on failure."""
    payload = {
        "quoteResponse": quote,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": {
            "priorityLevelWithMaxLamports": {
                "maxLamports": 1_000_000,
                "priorityLevel": "high",
            }
        },
    }
    async with aiohttp.ClientSession(headers=JUP_HEADERS) as s:
        async with s.post(JUP_SWAP, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                print(f"Swap HTTP {r.status}: {await r.text()}")
                return None
            swap_data = await r.json()
    swap_tx_b64 = swap_data.get("swapTransaction", "")
    if not swap_tx_b64:
        print("No swapTransaction in response")
        return None
    tx_bytes = base64.b64decode(swap_tx_b64)
    unsigned_tx = VersionedTransaction.from_bytes(tx_bytes)
    signed_tx = VersionedTransaction(unsigned_tx.message, [keypair])
    send_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            base64.b64encode(bytes(signed_tx)).decode("utf-8"),
            {"encoding": "base64", "skipPreflight": False},
        ],
    }
    result = await post_rpc(send_payload) or {}
    if "error" in result:
        print(f"Send error: {result['error']}")
        return None
    sig = result.get("result", "")
    if not sig:
        print(f"No signature returned: {result}")
        return None
    return sig


async def await_confirmation(sig: str, max_wait: float = 60.0) -> bool:
    import time
    deadline = time.time() + max_wait
    while time.time() < deadline:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [[sig], {"searchTransactionHistory": True}],
        }
        data = await post_rpc(payload) or {}
        statuses = (data.get("result") or {}).get("value") or []
        s = statuses[0] if statuses else None
        if s:
            err = s.get("err")
            cs = s.get("confirmationStatus")
            if err is not None:
                print(f"  TX failed on-chain: {err}")
                return False
            if cs in ("confirmed", "finalized"):
                print(f"  TX {cs}")
                return True
        await asyncio.sleep(2.0)
    print("  TX confirmation timeout")
    return False


async def get_sol_balance(owner: str) -> float:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [owner]}
    data = await post_rpc(payload) or {}
    lamports = (data.get("result") or {}).get("value", 0)
    return lamports / 1e9


async def main():
    keypair = Keypair.from_base58_string(PRIVATE_KEY)
    pub = str(keypair.pubkey())
    print(f"Wallet: {pub}")
    sol_pre = await get_sol_balance(pub)
    print(f"SOL balance pre: {sol_pre:.4f}")
    raw_balance = await get_token_balance_raw(pub, TRIPLET_MINT)
    print(f"TripleT raw balance: {raw_balance}")
    if raw_balance == 0:
        print("No TripleT to sell. Exiting.")
        return
    decimals = 6  # pump.fun convention
    ui_amount = raw_balance / (10 ** decimals)
    print(f"TripleT UI balance: {ui_amount:,.4f}")
    print()
    print("Fetching Jupiter quote (300 bps slippage tolerance)...")
    quote = await get_quote(TRIPLET_MINT, SOL_MINT, raw_balance, slippage_bps=300)
    if not quote:
        print("Quote failed. Exiting.")
        return
    sol_out = int(quote.get("outAmount", 0)) / 1e9
    impact = float(quote.get("priceImpactPct", 0))
    print(f"  Expected SOL out: {sol_out:.6f} (~${sol_out * 83.73:.2f})")
    print(f"  Price impact: {impact * 100:.2f}%")
    print()
    confirm = input("Proceed with sell? (yes/no): ").strip().lower()
    if confirm not in ("yes", "y"):
        print("Aborted.")
        return
    print("Submitting swap...")
    sig = await execute_swap(quote, keypair)
    if not sig:
        print("Swap submission failed.")
        return
    print(f"  TX sent: {sig}")
    print(f"  https://solscan.io/tx/{sig}")
    print("Waiting for confirmation...")
    ok = await await_confirmation(sig)
    if not ok:
        print("Confirmation failed.")
        return
    sol_post = await get_sol_balance(pub)
    raw_post = await get_token_balance_raw(pub, TRIPLET_MINT)
    print()
    print("=== RESULT ===")
    print(f"SOL: {sol_pre:.4f} → {sol_post:.4f} (Δ {sol_post - sol_pre:+.4f} ≈ ${(sol_post - sol_pre) * 83.73:+.2f})")
    print(f"TripleT raw: {raw_balance} → {raw_post}")


if __name__ == "__main__":
    asyncio.run(main())
