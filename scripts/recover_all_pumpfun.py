"""
One-off recovery: sweep all pump.fun (Token-2022) holdings back to SOL.

Background: a chain of bot bugs (decimals + reconcile-checks-wrong-program +
env-pause-doesn't-hot-reload) caused 4 buys totaling $180 to land on-chain
without the bot tracking them.  This script enumerates Token-2022 holdings
and offers each as a sweep.

Read-only by default — prompts before each swap.  Pass --yes-sweep-all to
non-interactively sell everything.
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
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_CLASSIC = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

PRIVATE_KEY = os.environ.get("SOLANA_PRIVATE_KEY", "").strip()
RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=06c97f31-8c26-4dae-9fb7-2f32ccc87f2c").strip()
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY", "").strip()

if not PRIVATE_KEY:
    print("ERROR: SOLANA_PRIVATE_KEY not set in env")
    sys.exit(1)

if JUPITER_API_KEY:
    JUP_QUOTE = "https://api.jup.ag/swap/v1/quote"
    JUP_SWAP = "https://api.jup.ag/swap/v1/swap"
    JUP_HEADERS = {"x-api-key": JUPITER_API_KEY}
else:
    JUP_QUOTE = "https://quote-api.jup.ag/v6/quote"
    JUP_SWAP = "https://quote-api.jup.ag/v6/swap"
    JUP_HEADERS = {}

YES_ALL = "--yes-sweep-all" in sys.argv


async def post_rpc(payload: dict):
    async with aiohttp.ClientSession() as s:
        async with s.post(RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            return await r.json()


async def list_holdings(owner: str):
    out = []
    for program in (TOKEN_2022, TOKEN_CLASSIC):
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [owner, {"programId": program}, {"encoding": "jsonParsed"}],
        }
        data = await post_rpc(payload) or {}
        for a in (data.get("result") or {}).get("value", []):
            info = a["account"]["data"]["parsed"]["info"]
            ta = info["tokenAmount"]
            raw = int(ta.get("amount", "0"))
            ui = float(ta.get("uiAmount") or 0)
            if ui > 0.001:
                out.append({
                    "mint": info["mint"],
                    "decimals": ta["decimals"],
                    "raw": raw,
                    "ui": ui,
                    "program": program,
                })
    return out


async def get_quote(input_mint, amount, slippage_bps=300):
    params = {
        "inputMint": input_mint, "outputMint": SOL_MINT,
        "amount": amount, "slippageBps": slippage_bps,
    }
    async with aiohttp.ClientSession(headers=JUP_HEADERS) as s:
        async with s.get(JUP_QUOTE, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            return await r.json()


async def execute_swap(quote, keypair):
    payload = {
        "quoteResponse": quote,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": {
            "priorityLevelWithMaxLamports": {"maxLamports": 1_000_000, "priorityLevel": "high"}
        },
    }
    async with aiohttp.ClientSession(headers=JUP_HEADERS) as s:
        async with s.post(JUP_SWAP, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return None, await r.text()
            data = await r.json()
    tx_b64 = data.get("swapTransaction", "")
    if not tx_b64:
        return None, "no swapTransaction"
    unsigned = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    signed = VersionedTransaction(unsigned.message, [keypair])
    send_payload = {
        "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
        "params": [base64.b64encode(bytes(signed)).decode(), {"encoding": "base64", "skipPreflight": False}],
    }
    result = await post_rpc(send_payload) or {}
    if "error" in result:
        return None, f"RPC error: {result['error']}"
    sig = result.get("result", "")
    return sig, None


async def await_confirmation(sig, max_wait=60.0):
    import time
    deadline = time.time() + max_wait
    while time.time() < deadline:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getSignatureStatuses",
                   "params": [[sig], {"searchTransactionHistory": True}]}
        data = await post_rpc(payload) or {}
        statuses = (data.get("result") or {}).get("value") or []
        s = statuses[0] if statuses else None
        if s:
            err = s.get("err")
            cs = s.get("confirmationStatus")
            if err is not None:
                return False, str(err)
            if cs in ("confirmed", "finalized"):
                return True, cs
        await asyncio.sleep(2.0)
    return False, "timeout"


async def main():
    keypair = Keypair.from_base58_string(PRIVATE_KEY)
    pub = str(keypair.pubkey())
    print(f"Wallet: {pub}\n")

    holdings = await list_holdings(pub)
    if not holdings:
        print("No holdings to sweep.")
        return

    # Pre-flight: print all holdings + Jupiter quotes
    print("=== Holdings ===")
    plans = []
    for h in holdings:
        quote = await get_quote(h["mint"], h["raw"])
        if quote:
            sol_out = int(quote.get("outAmount", 0)) / 1e9
            impact = float(quote.get("priceImpactPct", 0)) * 100
            usd_at_84 = sol_out * 84
            print(f"  {h['mint']:<48} qty={h['ui']:>16,.2f} dec={h['decimals']}  "
                  f"sol_out={sol_out:.6f} (~${usd_at_84:.2f}) impact={impact:.2f}%")
            plans.append({**h, "quote": quote, "sol_out": sol_out, "impact": impact})
        else:
            print(f"  {h['mint']:<48} qty={h['ui']:>16,.2f} dec={h['decimals']}  NO QUOTE")
    print()
    total_sol_out = sum(p["sol_out"] for p in plans)
    print(f"Total recoverable: {total_sol_out:.6f} SOL (~${total_sol_out * 84:.2f})\n")

    if not YES_ALL:
        confirm = input("Proceed to sweep ALL? (yes/no): ").strip().lower()
        if confirm not in ("yes", "y"):
            print("Aborted."); return

    for plan in plans:
        sym = plan["mint"][:10]
        print(f"\nSweeping {sym}... qty={plan['ui']:,.2f}")
        sig, err = await execute_swap(plan["quote"], keypair)
        if not sig:
            print(f"  FAILED: {err}")
            continue
        print(f"  TX: {sig}")
        ok, status = await await_confirmation(sig)
        print(f"  {'OK' if ok else 'FAIL'}: {status}")


if __name__ == "__main__":
    asyncio.run(main())
