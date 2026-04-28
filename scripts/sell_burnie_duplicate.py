"""
Targeted sell: clean up the duplicate BURNIE buy.

Bot's open_positions records BURNIE with 2,831.3402 tokens (from the second
buy). On-chain we hold 5,728.6690 (from two buys). Sell exactly the first-buy
portion (2,897.3287 tokens) so bot's record matches reality.
"""
import asyncio
import base64
import json
import os
import sys

import aiohttp
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction


SOL_MINT = "So11111111111111111111111111111111111111112"
BURNIE_MINT = "CGEDT9QZDvvH5GmVkWJH2BXiMJqMJySC9ihWyr7Spump"

# Sell exactly the first-buy amount (in raw atomic units: 6 decimals)
SELL_AMOUNT_RAW = 2_897_328_712  # = 2,897.328712 BURNIE

PRIVATE_KEY = os.environ.get("SOLANA_PRIVATE_KEY", "").strip()
RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=06c97f31-8c26-4dae-9fb7-2f32ccc87f2c").strip()
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY", "").strip()

if not PRIVATE_KEY:
    print("ERROR: SOLANA_PRIVATE_KEY not set"); sys.exit(1)

if JUPITER_API_KEY:
    JUP_QUOTE = "https://api.jup.ag/swap/v1/quote"
    JUP_SWAP = "https://api.jup.ag/swap/v1/swap"
    JUP_HEADERS = {"x-api-key": JUPITER_API_KEY}
else:
    JUP_QUOTE = "https://quote-api.jup.ag/v6/quote"
    JUP_SWAP = "https://quote-api.jup.ag/v6/swap"
    JUP_HEADERS = {}


async def post_rpc(payload):
    async with aiohttp.ClientSession() as s:
        async with s.post(RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            return await r.json()


async def main():
    keypair = Keypair.from_base58_string(PRIVATE_KEY)
    pub = str(keypair.pubkey())
    print(f"Wallet: {pub}")

    # Pre-balance
    p = {"jsonrpc":"2.0","id":1,"method":"getBalance","params":[pub]}
    sol_pre = (await post_rpc(p))["result"]["value"] / 1e9
    print(f"SOL pre:    {sol_pre:.4f}")

    # Quote
    params = {
        "inputMint": BURNIE_MINT,
        "outputMint": SOL_MINT,
        "amount": SELL_AMOUNT_RAW,
        "slippageBps": 300,
    }
    async with aiohttp.ClientSession(headers=JUP_HEADERS) as s:
        async with s.get(JUP_QUOTE, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            quote = await r.json()
    sol_out = int(quote.get("outAmount", 0)) / 1e9
    impact = float(quote.get("priceImpactPct", 0)) * 100
    print(f"\nQuote: sell {SELL_AMOUNT_RAW / 1e6:,.4f} BURNIE")
    print(f"  Expected SOL out: {sol_out:.6f} (~${sol_out * 84:.2f})")
    print(f"  Price impact: {impact:.2f}%")

    confirm = input("\nProceed? (yes/no): ").strip().lower()
    if confirm not in ("yes", "y"):
        print("Aborted."); return

    # Build + sign + submit
    payload = {
        "quoteResponse": quote,
        "userPublicKey": pub,
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": {
            "priorityLevelWithMaxLamports": {"maxLamports": 1_000_000, "priorityLevel": "high"}
        },
    }
    async with aiohttp.ClientSession(headers=JUP_HEADERS) as s:
        async with s.post(JUP_SWAP, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json()
    tx_b64 = data["swapTransaction"]
    unsigned = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    signed = VersionedTransaction(unsigned.message, [keypair])
    send_payload = {
        "jsonrpc":"2.0","id":1,"method":"sendTransaction",
        "params":[base64.b64encode(bytes(signed)).decode(), {"encoding":"base64","skipPreflight":False}],
    }
    result = await post_rpc(send_payload)
    sig = result["result"]
    print(f"\nTX sent: {sig}")
    print(f"https://solscan.io/tx/{sig}")

    # Wait for confirmation
    import time
    deadline = time.time() + 60
    while time.time() < deadline:
        p2 = {"jsonrpc":"2.0","id":1,"method":"getSignatureStatuses",
              "params":[[sig], {"searchTransactionHistory": True}]}
        d = await post_rpc(p2)
        status = (d.get("result") or {}).get("value", [{}])[0]
        if status:
            err = status.get("err")
            cs = status.get("confirmationStatus")
            if err is not None:
                print(f"FAILED: {err}"); return
            if cs in ("confirmed", "finalized"):
                print(f"  Confirmed.")
                break
        await asyncio.sleep(2)

    # Post-balance
    sol_post = (await post_rpc(p))["result"]["value"] / 1e9
    print(f"\nSOL post:   {sol_post:.4f}  (delta {sol_post - sol_pre:+.4f})")


if __name__ == "__main__":
    asyncio.run(main())
